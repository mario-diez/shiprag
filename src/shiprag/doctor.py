"""Diagnóstico de entorno offline (sin necesidad de UI)."""

from __future__ import annotations

import importlib.util
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
    checks.append(_ok("profile", f"{rt['profile']} · emb={rt['embedding_backend']} · rerank={rt['reranker_backend']}"))
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
