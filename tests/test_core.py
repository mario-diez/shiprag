"""Tests unitarios del núcleo ShipRAG (sin modelos pesados)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import (
    Criticality,
    DocType,
    DocumentMeta,
    QueryRequest,
    ResponseMode,
    Zone,
)
from shiprag.experts.router import QueryRouter
from shiprag.generation.generator import ExtractiveGenerator
from shiprag.generation.verifier import AnswerVerifier, grounding_score
from shiprag.index.embeddings import HashEmbedding
from shiprag.index.store import HybridIndex
from shiprag.ingest.chunker import StructuralChunker
from shiprag.ingest.pdf_extractor import DocumentExtractor
from shiprag.ingest.pipeline import IngestPipeline
from shiprag.orchestration.pipeline import Orchestrator
from shiprag.retrieval.hybrid import HybridRetriever, rrf_fuse
from shiprag.retrieval.reranker import LexicalReranker
from shiprag.core.schemas import Chunk, ScoredChunk


@pytest.fixture()
def tmp_cfg(tmp_path: Path) -> AppConfig:
    from shiprag.core.config import PathsConfig

    base = load_config()
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
    return cfg


def test_router_emergency_mob():
    r = QueryRouter(load_config())
    d = r.route("hombre al agua por estribor, ¿qué hago?")
    assert d.emergency is True
    assert Zone.EMERGENCIAS in d.zones
    assert d.suggested_mode == ResponseMode.EXTRACTIVE


def test_router_maquinaria_code():
    r = QueryRouter(load_config())
    d = r.route("alarma FO-12 del generador en sala de máquinas")
    assert Zone.MAQUINARIA in d.zones or Zone.ELECTRICIDAD in d.zones


def test_structural_chunker_keeps_procedure(tmp_cfg):
    sample = Path(__file__).resolve().parents[1] / "data" / "sample" / "emergencias_hombre_al_agua.txt"
    blocks = DocumentExtractor(tmp_cfg).extract(sample)
    meta = DocumentMeta(
        doc_id="mob",
        title="MOB",
        source_path=str(sample),
        zone=Zone.EMERGENCIAS,
        doc_type=DocType.EMERGENCY,
        criticality=Criticality.CRITICAL,
    )
    chunks = StructuralChunker(tmp_cfg).chunk(meta, blocks)
    assert chunks
    assert any(c.is_numbered_procedure for c in chunks)
    assert any(c.has_safety_warning for c in chunks)


def test_hybrid_retrieve_and_answer(tmp_cfg):
    embedder = HashEmbedding(dim=64)
    index = HybridIndex(tmp_cfg, embedder=embedder)
    sample = Path(__file__).resolve().parents[1] / "data" / "sample"
    IngestPipeline(tmp_cfg, index=index).ingest_dir(sample)
    orch = Orchestrator(tmp_cfg, index=index)
    resp = orch.query(
        QueryRequest(
            query="procedimiento hombre al agua acciones del puente",
            emergency=True,
            mode=ResponseMode.EXTRACTIVE,
        )
    )
    assert resp.status.value in {"ok", "clarify", "conflict"}
    assert resp.citations
    assert resp.trace is not None
    assert "hombre" in resp.answer.lower() or any(
        "hombre" in c.quote.lower() for c in resp.citations
    )


def test_abstain_on_irrelevant(tmp_cfg):
    embedder = HashEmbedding(dim=64)
    index = HybridIndex(tmp_cfg, embedder=embedder)
    sample = Path(__file__).resolve().parents[1] / "data" / "sample"
    IngestPipeline(tmp_cfg, index=index).ingest_dir(sample)
    orch = Orchestrator(tmp_cfg, index=index)
    resp = orch.query(QueryRequest(query="receta de paella valenciana con marisco"))
    assert resp.status.value in {"not_found", "abstain"}


def test_rrf_and_rerank():
    ch1 = Chunk(
        chunk_id="a",
        doc_id="d",
        text="hombre al agua procedimiento puente",
        page_start=1,
        page_end=1,
    )
    ch2 = Chunk(
        chunk_id="b",
        doc_id="d",
        text="otro texto irrelevante generador aceite",
        page_start=2,
        page_end=2,
    )
    fused = rrf_fuse([(ch1, 5.0, 1), (ch2, 1.0, 2)], [(ch2, 0.9, 1), (ch1, 0.5, 2)])
    assert fused[0].chunk.chunk_id in {"a", "b"}
    reranked = LexicalReranker(emergency_bias=True).rerank(
        "hombre al agua puente",
        [
            ScoredChunk(chunk=ch1, score=0.1, rrf_score=0.1),
            ScoredChunk(chunk=ch2, score=0.2, rrf_score=0.2),
        ],
        top_k=2,
    )
    assert reranked[0].chunk.chunk_id == "a"


def test_profiles_lite_forces_hash():
    from shiprag.core.config import clear_config_cache, load_config
    from shiprag.index.embeddings import build_embedder
    from shiprag.retrieval.reranker import LexicalReranker, build_reranker

    clear_config_cache()
    cfg = load_config(profile="lite")
    assert cfg.profile.id == "lite"
    assert cfg.models.embedding.backend == "hash"
    assert cfg.models.llm.enabled is False
    assert "lite" in cfg.paths.index_dir
    emb = build_embedder(cfg)
    assert emb.name.startswith("hash")
    rr = build_reranker(cfg)
    assert isinstance(rr, LexicalReranker)


def test_profile_home_and_server_exist():
    from shiprag.core.config import clear_config_cache, list_profiles, load_config

    clear_config_cache()
    ids = {p["id"] for p in list_profiles()}
    assert {"lite", "home", "server"} <= ids
    home = load_config(profile="home")
    server = load_config(profile="server")
    assert home.paths.index_dir != server.paths.index_dir
    assert home.models.llm.enabled is False


def test_doctor_lite_ok():
    from shiprag.core.config import clear_config_cache, load_config
    from shiprag.doctor import run_doctor

    clear_config_cache()
    report = run_doctor(load_config(profile="lite"))
    assert report["ok"] is True
    assert report["runtime"]["profile"] == "lite"


def test_sidecar_meta_ingest(tmp_cfg, tmp_path: Path):
    from shiprag.core.config import clear_config_cache
    from shiprag.index.embeddings import HashEmbedding
    from shiprag.index.store import HybridIndex
    from shiprag.ingest.pipeline import IngestPipeline

    clear_config_cache()
    src = Path(__file__).resolve().parents[1] / "data" / "sample" / "cubierta_amarre.txt"
    meta = Path(__file__).resolve().parents[1] / "data" / "sample" / "cubierta_amarre.meta.yaml"
    work = tmp_path / "doc"
    work.mkdir()
    shutil = __import__("shutil")
    shutil.copy2(src, work / src.name)
    shutil.copy2(meta, work / meta.name)
    index = HybridIndex(tmp_cfg, embedder=HashEmbedding(dim=64))
    result = IngestPipeline(tmp_cfg, index=index).ingest_file(work / src.name)
    assert result["zone"] == "cubierta"
    assert result["doc_id"] == "deck-moor-01"

    ch = Chunk(
        chunk_id="c1",
        doc_id="d",
        text="Pulsar el botón DISTRESS en VHF DSC y llamar MAYDAY en CH16.",
        page_start=1,
        page_end=1,
    )
    items = [ScoredChunk(chunk=ch, score=0.8)]
    g_good = grounding_score("Pulsar DISTRESS y llamar MAYDAY en CH16", items)
    g_bad = grounding_score("Usar teletransporte cuántico para evacuar Marte", items)
    assert g_good > g_bad
