"""Ciclo de vida de documentos en el índice: versiones, borrado, colisiones, lotes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from shiprag.core.config import AppConfig, PathsConfig, clear_config_cache, load_config
from shiprag.index.embeddings import HashEmbedding
from shiprag.index.store import HybridIndex
from shiprag.ingest.pipeline import IngestPipeline

DOC_V1 = """# Procedimiento de purga de aire comprimido
# Zona: maquinaria

## 1. Pasos
1. Abrir la válvula de purga del calderín ALFA-7.
2. Esperar a que salga todo el condensado.
"""

DOC_V2 = """# Procedimiento de purga de aire comprimido
# Zona: maquinaria

## 1. Pasos
1. Abrir la válvula de purga del calderín BETA-9.
2. Cerrar cuando no salga agua.
"""


class CountingEmbedding(HashEmbedding):
    def __init__(self, dim: int = 64) -> None:
        super().__init__(dim=dim)
        self.doc_calls = 0
        self.docs_embedded = 0

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        self.doc_calls += 1
        self.docs_embedded += len(texts)
        return super().embed_documents(texts)


@pytest.fixture()
def cfg(tmp_path: Path) -> AppConfig:
    clear_config_cache()
    base = load_config(profile="lite")
    data = tmp_path / "data"
    out = base.model_copy(deep=True)
    out.paths = PathsConfig(
        data_dir=str(data),
        raw_dir=str(data / "raw"),
        index_dir=str(data / "indexes"),
        log_dir=str(data / "logs"),
        models_dir=str(tmp_path / "models"),
        sample_dir=str(Path(__file__).resolve().parents[1] / "data" / "sample"),
    )
    return out


def _texts(index: HybridIndex, zone: str, query: str) -> list[str]:
    return [ch.text for ch, _, _ in index.lexical(zone).search(query, top_k=50)]


def test_reingest_replaces_previous_version(cfg: AppConfig, tmp_path: Path):
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    doc = tmp_path / "src" / "purga.txt"
    doc.parent.mkdir()
    doc.write_text(DOC_V1, encoding="utf-8")

    first = pipe.ingest_file(doc)
    assert first["action"] == "created"
    assert first["zone"] == "maquinaria"
    assert any("ALFA-7" in t for t in _texts(index, "maquinaria", "ALFA-7"))

    doc.write_text(DOC_V2, encoding="utf-8")
    second = pipe.ingest_file(doc)
    assert second["action"] == "updated"
    assert second["doc_id"] == first["doc_id"]
    assert second["chunks_replaced"] == first["chunks"]

    for zone in ("maquinaria", "general"):
        assert not any("ALFA-7" in t for t in _texts(index, zone, "ALFA-7")), zone
        assert any("BETA-9" in t for t in _texts(index, zone, "BETA-9")), zone
        assert len(index.lexical(zone)) == second["chunks"]
        assert len(index.dense(zone)) == second["chunks"]


def test_unchanged_document_is_skipped_unless_forced(cfg: AppConfig, tmp_path: Path):
    embedder = CountingEmbedding()
    pipe = IngestPipeline(cfg, index=HybridIndex(cfg, embedder=embedder))
    doc = tmp_path / "purga.txt"
    doc.write_text(DOC_V1, encoding="utf-8")

    pipe.ingest_file(doc)
    calls = embedder.doc_calls
    again = pipe.ingest_file(doc)
    assert again["action"] == "unchanged"
    assert embedder.doc_calls == calls

    forced = pipe.ingest_file(doc, force=True)
    assert forced["action"] == "updated"
    assert embedder.doc_calls == calls + 1


def test_embeddings_computed_once_for_zone_and_general_mirror(cfg: AppConfig, tmp_path: Path):
    embedder = CountingEmbedding()
    pipe = IngestPipeline(cfg, index=HybridIndex(cfg, embedder=embedder))
    doc = tmp_path / "purga.txt"
    doc.write_text(DOC_V1, encoding="utf-8")
    res = pipe.ingest_file(doc)
    # Antes se calculaban dos veces (zona + espejo general)
    assert embedder.docs_embedded == res["chunks"]


def test_same_filename_in_two_folders_does_not_collide(cfg: AppConfig, tmp_path: Path):
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    a = tmp_path / "dep_a" / "manual.txt"
    b = tmp_path / "dep_b" / "manual.txt"
    for p, text in ((a, DOC_V1), (b, DOC_V2)):
        p.parent.mkdir()
        p.write_text(text, encoding="utf-8")

    res = pipe.ingest_dir(tmp_path)
    ids = {r["doc_id"] for r in res}
    assert len(ids) == 2
    assert any("ALFA-7" in t for t in _texts(index, "general", "ALFA-7"))
    assert any("BETA-9" in t for t in _texts(index, "general", "BETA-9"))
    # Las copias en raw tampoco se pisan
    raws = {Path(r["source_path"]) for r in res}
    assert len(raws) == 2 and all(p.exists() for p in raws)


def test_delete_document_removes_everything(cfg: AppConfig, tmp_path: Path):
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    doc = tmp_path / "purga.txt"
    doc.write_text(DOC_V1, encoding="utf-8")
    res = pipe.ingest_file(doc)
    raw_copy = Path(res["source_path"])
    assert raw_copy.exists()

    out = pipe.delete_document(res["doc_id"])
    assert out["chunks_removed"] == res["chunks"]
    for zone in ("maquinaria", "general"):
        assert len(index.lexical(zone)) == 0
        assert len(index.dense(zone)) == 0
    assert res["doc_id"] not in pipe.registry
    assert not raw_copy.exists()
    assert doc.exists(), "nunca se borra el original fuera de data/raw"
    with pytest.raises(KeyError):
        pipe.delete_document(res["doc_id"])


def test_delete_never_removes_an_original_stored_inside_raw(cfg: AppConfig):
    """Si alguien ingiere en su sitio desde data/raw, borrar del índice no borra su fichero."""
    pipe = IngestPipeline(cfg, index=HybridIndex(cfg, embedder=HashEmbedding(dim=64)))
    original = cfg.raw_path / "mis_docs" / "purga.txt"
    original.parent.mkdir(parents=True)
    original.write_text(DOC_V1, encoding="utf-8")
    res = pipe.ingest_file(original, copy_to_raw=False)
    pipe.delete_document(res["doc_id"])
    assert original.exists()

    uploaded = cfg.raw_path / "uploads" / "subido.txt"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_text(DOC_V2, encoding="utf-8")
    res = pipe.ingest_file(uploaded, copy_to_raw=False)
    pipe.delete_document(res["doc_id"])
    assert not uploaded.exists(), "las subidas por API sí son de ShipRAG"


def test_prune_removes_documents_whose_source_disappeared(cfg: AppConfig, tmp_path: Path):
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    src = tmp_path / "docs"
    src.mkdir()
    (src / "uno.txt").write_text(DOC_V1, encoding="utf-8")
    (src / "dos.txt").write_text(DOC_V2, encoding="utf-8")
    pipe.ingest_dir(src)
    assert len(pipe.registry) == 2

    (src / "dos.txt").unlink()
    res = pipe.ingest_dir(src, prune=True)
    actions = sorted(r["action"] for r in res)
    assert actions == ["pruned", "unchanged"]
    assert len(pipe.registry) == 1
    assert not any("BETA-9" in t for t in _texts(index, "general", "BETA-9"))


def test_ingest_dir_rebuilds_bm25_once_per_store(cfg: AppConfig, tmp_path: Path):
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    src = tmp_path / "docs"
    src.mkdir()
    for i in range(6):
        (src / f"doc{i}.txt").write_text(DOC_V1.replace("ALFA-7", f"ALFA-{i}"), encoding="utf-8")
    pipe.ingest_dir(src)
    assert index.lexical("maquinaria").commits == 1
    assert index.lexical("general").commits == 1
    assert len(index.lexical("general")) == len(index.lexical("maquinaria")) > 0


def test_index_survives_reload(cfg: AppConfig, tmp_path: Path):
    doc = tmp_path / "purga.txt"
    doc.write_text(DOC_V1, encoding="utf-8")
    pipe = IngestPipeline(cfg, index=HybridIndex(cfg, embedder=HashEmbedding(dim=64)))
    res = pipe.ingest_file(doc)

    reloaded = IngestPipeline(cfg, index=HybridIndex(cfg, embedder=HashEmbedding(dim=64)))
    assert reloaded.registry.get(res["doc_id"]).chunk_count == res["chunks"]
    assert any("ALFA-7" in t for t in _texts(reloaded.index, "maquinaria", "ALFA-7"))
    assert reloaded.ingest_file(doc)["action"] == "unchanged"


def test_legacy_registry_entry_is_claimed_and_replaced(cfg: AppConfig, tmp_path: Path):
    """Índices creados antes del registro: sin source_uri; la reingesta los sustituye."""
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    doc = tmp_path / "purga.txt"
    doc.write_text(DOC_V1, encoding="utf-8")
    res = pipe.ingest_file(doc)
    meta = pipe.registry.get(res["doc_id"])
    legacy = meta.model_copy(update={"source_uri": "", "content_hash": "", "ingest_fingerprint": ""})
    pipe.registry.put(legacy)

    doc.write_text(DOC_V2, encoding="utf-8")
    fresh = IngestPipeline(cfg, index=index)
    again = fresh.ingest_file(doc)
    assert again["doc_id"] == res["doc_id"]
    assert again["action"] == "updated"
    assert not any("ALFA-7" in t for t in _texts(index, "general", "ALFA-7"))


def test_search_during_batch_sees_consistent_snapshot(cfg: AppConfig, tmp_path: Path):
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=64))
    pipe = IngestPipeline(cfg, index=index)
    doc = tmp_path / "purga.txt"
    doc.write_text(DOC_V1, encoding="utf-8")
    res = pipe.ingest_file(doc)
    with index.batch():
        pipe.delete_document(res["doc_id"])
        # El borrado aún no está publicado: la búsqueda usa el snapshot anterior sin romper
        assert any("ALFA-7" in t for t in _texts(index, "general", "ALFA-7"))
    assert not _texts(index, "general", "ALFA-7")
