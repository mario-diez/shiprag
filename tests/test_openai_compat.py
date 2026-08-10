"""Tests del endpoint OpenAI-compatible (Open WebUI)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from shiprag.api.app import create_app
from shiprag.core.config import PathsConfig, clear_config_cache, load_config
from shiprag.index.embeddings import HashEmbedding
from shiprag.index.store import HybridIndex
from shiprag.ingest.pipeline import IngestPipeline


def test_openai_models_and_chat(tmp_path: Path):
    clear_config_cache()
    base = load_config(profile="lite")
    data = tmp_path / "data"
    cfg = base.model_copy(deep=True)
    cfg.paths = PathsConfig(
        data_dir=str(data),
        raw_dir=str(data / "raw"),
        index_dir=str(data / "indexes"),
        log_dir=str(data / "logs"),
        models_dir=str(tmp_path / "models"),
        sample_dir=str(Path(__file__).resolve().parents[1] / "data" / "sample"),
    )
    # Misma dimensión que usará create_app / build_embedder(lite)
    dim = int(cfg.models.embedding.dim or 384)
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=dim))
    # Solo TXT para el test (más rápido / estable)
    sample = Path(cfg.paths.sample_dir)
    for f in sorted(sample.glob("*.txt")):
        IngestPipeline(cfg, index=index).ingest_file(f)

    client = TestClient(create_app(cfg))

    models = client.get("/v1/models")
    assert models.status_code == 200
    ids = {m["id"] for m in models.json()["data"]}
    assert "shiprag" in ids
    assert "shiprag-emergency" in ids

    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "shiprag-emergency",
            "stream": False,
            "messages": [
                {"role": "user", "content": "procedimiento hombre al agua"},
            ],
        },
    )
    assert chat.status_code == 200, chat.text
    body = chat.json()
    content = body["choices"][0]["message"]["content"]
    assert "Fuentes" in content or "HOMBRE" in content.upper() or "NO TENGO" in content
    assert body["model"] == "shiprag-emergency"
