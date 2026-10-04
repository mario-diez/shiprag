"""Diagnóstico de entorno offline (sin necesidad de UI)."""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from shiprag import __version__
from shiprag.core.config import AppConfig, ROOT, list_profiles, load_config


def _ok(name: str, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": True, "detail": detail}


def _fail(name: str, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": False, "detail": detail}


def _warn(name: str, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": True, "warn": True, "detail": detail}


def _llm_http_check(base_url: str, model: str, api_key_env: str) -> dict[str, Any]:
    if not base_url or not model:
        return _fail("llm_http", "llm.backend=openai_compat requiere base_url y model")
    try:
        import httpx

        key = os.environ.get(api_key_env, "") if api_key_env else ""
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        r = httpx.get(base_url.rstrip("/") + "/models", headers=headers, timeout=3.0)
        if r.status_code < 400:
            return _ok("llm_http", f"{base_url} responde · modelo {model}")
        return _warn("llm_http", f"{base_url}/models → HTTP {r.status_code}")
    except Exception as exc:
        return _warn("llm_http", f"{base_url} no responde ({type(exc).__name__}) — usará extractivo")


def run_doctor(cfg: AppConfig | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    checks: list[dict[str, Any]] = []

    # Python
    py = sys.version_info
    if py >= (3, 11):
        checks.append(_ok("python", f"{platform.python_version()} ({platform.system()})"))
    else:
        checks.append(_fail("python", f"Se requiere >=3.11, hay {platform.python_version()}"))

    # Dependencias clave
    for mod in ("fastapi", "yaml", "numpy", "rank_bm25", "fitz", "pydantic"):
        if importlib.util.find_spec(mod if mod != "yaml" else "yaml"):
            checks.append(_ok(f"dep:{mod}", "instalado"))
        else:
            checks.append(_fail(f"dep:{mod}", "faltante — pip install -e '.[dev]'"))

    # sentence_transformers es opcional en lite
    if importlib.util.find_spec("sentence_transformers"):
        checks.append(_ok("dep:sentence_transformers", "disponible (home/server)"))
    else:
        checks.append(_warn("dep:sentence_transformers", "ausente — OK para perfil lite"))

    # Perfil / backends
    rt = cfg.runtime_summary()
    checks.append(
        _ok(
            "profile",
            f"{rt['profile']} · emb={rt['embedding_backend']} · "
            f"rerank={rt['reranker_backend']} · ver={rt.get('verifier_backend', '?')}",
        )
    )
    if cfg.profile.id == "lite" and cfg.models.embedding.backend != "hash":
        checks.append(_warn("profile_consistency", "lite debería usar embedding.backend=hash"))

    # Rutas
    sample = cfg.resolve(cfg.paths.sample_dir)
    if sample.exists() and any(sample.rglob("*.txt")):
        n = len(list(sample.rglob("*.txt"))) + len(list(sample.rglob("*.pdf")))
        checks.append(_ok("sample_docs", f"{n} ficheros en {sample}"))
    else:
        checks.append(_fail("sample_docs", f"No hay sample en {sample}"))

    # Modelos (solo aviso)
    emb = cfg.resolve(cfg.models.embedding.name_or_path) if cfg.models.embedding.name_or_path else None
    if cfg.profile.id in {"home", "balanced", "workstation", "server"}:
        if emb and emb.exists():
            checks.append(_ok("embedding_weights", str(emb)))
        else:
            checks.append(
                _warn(
                    "embedding_weights",
                    f"No hay pesos en {cfg.models.embedding.name_or_path} — usará fallback",
                )
            )
    else:
        checks.append(_ok("embedding_weights", "no requeridos en lite"))

    # LLM
    llm = cfg.models.llm
    backend = (llm.backend or "llama_cpp").lower()
    if not llm.enabled:
        checks.append(_ok("llm", "desactivado (respuestas extractivas)"))
    elif backend in {"openai_compat", "openai", "http"}:
        checks.append(_llm_http_check(llm.base_url, llm.model, llm.api_key_env))
    else:
        gguf = cfg.resolve(llm.name_or_path)
        if gguf.exists():
            checks.append(_ok("llm_weights", str(gguf)))
        else:
            checks.append(_warn("llm_weights", f"No existe {llm.name_or_path} — usará extractivo"))
        if importlib.util.find_spec("llama_cpp") is None:
            checks.append(_warn("dep:llama_cpp", "ausente — pip install -e '.[llm]'"))
        elif llm.n_gpu_layers > 0:
            try:
                import llama_cpp

                gpu = bool(llama_cpp.llama_supports_gpu_offload())
            except Exception:
                gpu = False
            if gpu:
                checks.append(_ok("llm_gpu", "llama-cpp-python con offload a GPU"))
            else:
                checks.append(
                    _warn(
                        "llm_gpu",
                        f"n_gpu_layers={llm.n_gpu_layers} pero llama-cpp-python está compilado "
                        "sin GPU: el LLM correrá en CPU (lento). Instala un wheel con CUDA "
                        "o usa llm.backend=openai_compat (vLLM/Ollama).",
                    )
                )

    # Seguridad de escritura
    token_env = cfg.api.admin_token_env
    if token_env and os.environ.get(token_env):
        checks.append(_ok("admin_token", f"{token_env} definido: ingesta/borrado protegidos"))
    else:
        checks.append(
            _warn("admin_token", f"{token_env or '(sin variable)'} vacío: ingesta/borrado abiertos")
        )

    # Disco
    usage = shutil.disk_usage(str(ROOT))
    free_gb = usage.free / (1024**3)
    if free_gb >= 1.0:
        checks.append(_ok("disk_free", f"{free_gb:.1f} GB libres"))
    else:
        checks.append(_warn("disk_free", f"Solo {free_gb:.1f} GB libres"))

    # OCR binario (opcional)
    if cfg.ocr.enabled:
        if shutil.which("tesseract"):
            checks.append(_ok("tesseract", shutil.which("tesseract") or ""))
        else:
            checks.append(_warn("tesseract", "no encontrado — OCR deshabilitado en la práctica"))
    else:
        checks.append(_ok("tesseract", "OCR desactivado en este perfil"))

    # Índice
    idx = cfg.index_path
    if idx.exists() and any(idx.rglob("*")):
        checks.append(_ok("index", f"existe {idx}"))
    else:
        checks.append(_warn("index", f"vacío — ejecutar: shiprag --profile {cfg.profile.id} ingest data/sample"))

    failed = [c for c in checks if not c["ok"]]
    warned = [c for c in checks if c.get("warn")]
    return {
        "version": __version__,
        "root": str(ROOT),
        "runtime": rt,
        "profiles": [p["id"] for p in list_profiles()],
        "ok": len(failed) == 0,
        "failed": len(failed),
        "warnings": len(warned),
        "checks": checks,
        "next_steps": [
            f"shiprag --profile {cfg.profile.id} ingest data/sample",
            f"shiprag --profile {cfg.profile.id} smoke",
            f"shiprag --profile {cfg.profile.id} serve --port 8080",
        ],
    }
