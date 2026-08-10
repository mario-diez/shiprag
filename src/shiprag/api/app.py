"""API FastAPI 100% local."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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
from shiprag.ingest.pipeline import IngestPipeline
from shiprag.orchestration.pipeline import Orchestrator
from shiprag.api.openai_compat import build_openai_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"


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

    def get_orch() -> Orchestrator:
        if state["orch"] is None:
            state["orch"] = Orchestrator(state["cfg"])
            state["ingest"] = IngestPipeline(state["cfg"], index=state["orch"].index)
        return state["orch"]

    def get_ingest() -> IngestPipeline:
        get_orch()
        assert state["ingest"] is not None
        return state["ingest"]

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

    @app.post("/api/ingest")
    async def ingest(
        file: UploadFile = File(...),
        zone: str | None = Form(default=None),
        doc_type: str | None = Form(default=None),
        title: str | None = Form(default=None),
        version: str = Form(default="1.0"),
        language: str = Form(default="es"),
    ) -> dict[str, Any]:
        raw_dir = state["cfg"].raw_path / "uploads"
        raw_dir.mkdir(parents=True, exist_ok=True)
        dest = raw_dir / (file.filename or "upload.bin")
        content = await file.read()
        dest.write_bytes(content)
        z = Zone(zone) if zone else None
        dt = DocType(doc_type) if doc_type else None
        try:
            result = get_ingest().ingest_file(
                dest,
                zone=z,
                doc_type=dt,
                title=title,
                version=version,
                language=language,
                copy_to_raw=False,
            )
        except Exception as exc:
            raise HTTPException(400, f"Ingesta fallida: {exc}") from exc
        return result

    @app.get("/api/documents")
    def list_documents() -> list[dict[str, Any]]:
        docs_dir = state["cfg"].index_path / "documents"
        if not docs_dir.exists():
            return []
        out = []
        for p in sorted(docs_dir.glob("*.json")):
            out.append(__import__("json").loads(p.read_text(encoding="utf-8")))
        return out

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
