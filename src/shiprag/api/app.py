"""API FastAPI 100% local."""

from __future__ import annotations

import os
import re
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from shiprag import __version__
from shiprag.core.config import (
    AppConfig,
    clear_config_cache,
    ensure_runtime_dirs,
    list_profiles,
    load_config,
)
from shiprag.core.logging import setup_logging
from shiprag.core.schemas import DocType, QueryRequest, QueryResponse, Zone
from shiprag.ingest.pdf_extractor import SUPPORTED_EXTENSIONS
from shiprag.ingest.pipeline import IngestPipeline
from shiprag.ingest.registry import DocumentRegistry
from shiprag.orchestration.pipeline import Orchestrator
from shiprag.api.openai_compat import build_openai_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"

UNSAFE_NAME_RE = re.compile(r"[^\w.\- ]+", re.UNICODE)
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


def safe_upload_name(filename: str | None) -> str:
    """Nombre base sin rutas ni caracteres peligrosos (evita path traversal: "../../x")."""
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = UNSAFE_NAME_RE.sub("_", name).strip(" .")[:180]
    if Path(name).stem.upper() in WINDOWS_RESERVED:
        name = f"_{name}"
    return name or "upload.bin"


def create_app(cfg: AppConfig | None = None) -> FastAPI:
    clear_config_cache()
    cfg = cfg or load_config()
    ensure_runtime_dirs(cfg)
    logger = setup_logging(cfg)

    app = FastAPI(
        title="ShipRAG",
        description="RAG offline para documentación técnica y emergencias de buque",
        version=__version__,
    )

    state: dict[str, Any] = {"orch": None, "ingest": None, "cfg": cfg}
    init_lock = threading.Lock()

    def get_orch() -> Orchestrator:
        # Doble comprobación: dos peticiones simultáneas no deben cargar los modelos dos veces
        if state["orch"] is None:
            with init_lock:
                if state["orch"] is None:
                    orch = Orchestrator(state["cfg"])
                    state["ingest"] = IngestPipeline(state["cfg"], index=orch.index)
                    state["orch"] = orch
        return state["orch"]

    def get_ingest() -> IngestPipeline:
        get_orch()
        assert state["ingest"] is not None
        return state["ingest"]

    token_env = cfg.api.admin_token_env

    def admin_token() -> str:
        # Se lee en cada petición: rotar el token no exige reiniciar
        return os.environ.get(token_env, "") if token_env else ""

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        expected = admin_token()
        if not expected:
            return
        provided = (authorization or "").strip()
        if provided.lower().startswith("bearer "):
            provided = provided[7:].strip()
        if not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(
                401,
                "Token de administración requerido para esta operación",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if not admin_token():
        logger.warning(
            "Ingesta y borrado SIN protección: define %s para exigir token de administración",
            token_env or "api.admin_token_env",
        )

    # OpenAI-compatible API (Open WebUI, clients OpenAI, etc.)
    app.include_router(build_openai_router(get_orch))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        orch = get_orch()
        return {
            "status": "ok",
            "version": __version__,
            "offline": True,
            "runtime": state["cfg"].runtime_summary(),
            "zones_indexed": orch.index.zones_with_data(),
            "embedder": orch.index.embedder.name,
            "generator": getattr(orch.generator, "name", "extractive"),
            "documents": len(get_ingest().registry),
            "write_protected": bool(admin_token()),
            "openai_compat": {
                "models": "/v1/models",
                "chat": "/v1/chat/completions",
                "openwebui_docs": "docs/OPENWEBUI.md",
            },
        }

    @app.get("/api/profiles")
    def profiles() -> list[dict[str, str]]:
        return list_profiles()

    @app.get("/api/zones")
    def zones() -> list[dict[str, Any]]:
        return [
            {
                "id": z.id,
                "label": z.label,
                "keywords": z.keywords,
                "default_response_mode": z.default_response_mode,
                "criticality": z.criticality,
            }
            for z in state["cfg"].zones
        ]

    @app.post("/api/query", response_model=QueryResponse)
    def query(req: QueryRequest) -> QueryResponse:
        if not req.query.strip():
            raise HTTPException(400, "query vacía")
        return get_orch().query(req)

    @app.post("/api/ingest", dependencies=[Depends(require_admin)])
    async def ingest(
        file: UploadFile = File(...),
        zone: str | None = Form(default=None),
        doc_type: str | None = Form(default=None),
        title: str | None = Form(default=None),
        version: str = Form(default="1.0"),
        language: str = Form(default="es"),
        force: bool = Form(default=False),
    ) -> dict[str, Any]:
        name = safe_upload_name(file.filename)
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                415,
                f"Formato no soportado: '{Path(name).suffix or name}'. "
                f"Admitidos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
        try:
            z = Zone(zone) if zone else None
            dt = DocType(doc_type) if doc_type else None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        raw_dir = state["cfg"].raw_path / "uploads"
        raw_dir.mkdir(parents=True, exist_ok=True)
        dest = raw_dir / name
        limit = int(state["cfg"].api.max_upload_mb) * 1024 * 1024
        # Volcado por bloques a un temporal: tope de tamaño sin cargar todo en memoria,
        # y el fichero anterior no se pisa hasta tener la subida completa
        tmp = raw_dir / f".{uuid.uuid4().hex}.part"
        size = 0
        too_big = False
        with tmp.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    too_big = True
                    break
                fh.write(chunk)
        if too_big:
            tmp.unlink(missing_ok=True)
            raise HTTPException(413, f"Fichero mayor que {state['cfg'].api.max_upload_mb} MB")
        os.replace(tmp, dest)

        def _run() -> dict[str, Any]:
            return get_ingest().ingest_file(
                dest,
                zone=z,
                doc_type=dt,
                title=title,
                version=version,
                language=language,
                copy_to_raw=False,
                force=force,
            )

        try:
            # Extracción/embeddings pueden tardar minutos: fuera del event loop
            return await run_in_threadpool(_run)
        except Exception as exc:
            raise HTTPException(400, f"Ingesta fallida: {exc}") from exc

    @app.get("/api/documents")
    def list_documents() -> list[dict[str, Any]]:
        registry = (
            state["ingest"].registry
            if state["ingest"] is not None
            else DocumentRegistry(state["cfg"].index_path / "documents")
        )
        return [m.model_dump(mode="json") for m in registry.all()]

    @app.delete("/api/documents/{doc_id}", dependencies=[Depends(require_admin)])
    def delete_document(doc_id: str) -> dict[str, Any]:
        try:
            return get_ingest().delete_document(doc_id)
        except KeyError as exc:
            raise HTTPException(404, f"Documento no encontrado: {doc_id}") from exc

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def ui_index() -> FileResponse:
        index = STATIC_DIR / "index.html"
        if not index.exists():
            raise HTTPException(404, "UI no encontrada")
        return FileResponse(index)

    logger.info("ShipRAG API lista · profile=%s", cfg.profile.id)
    return app


# Compatibilidad: `uvicorn shiprag.api.app:app`
app = create_app()
