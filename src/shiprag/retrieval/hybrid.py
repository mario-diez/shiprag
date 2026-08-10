"""Recuperación híbrida con fusión RRF y filtros de metadatos."""

from __future__ import annotations

import logging
from typing import Any

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import (
    Chunk,
    Criticality,
    DocType,
    RetrievalFilters,
    ScoredChunk,
    Zone,
)
from shiprag.index.store import HybridIndex

logger = logging.getLogger("shiprag.retrieval")

CRIT_ORDER = {
    Criticality.LOW: 0,
    Criticality.MEDIUM: 1,
    Criticality.HIGH: 2,
    Criticality.CRITICAL: 3,
}


def chunk_from_meta(chunk_id: str, document: str, meta: dict[str, Any]) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=str(meta.get("doc_id", "")),
        text=document,
        page_start=int(meta.get("page_start", 1)),
        page_end=int(meta.get("page_end", meta.get("page_start", 1))),
        section=meta.get("section") or None,
        heading_path=[p for p in str(meta.get("heading_path", "")).split(" > ") if p],
        zone=Zone(meta.get("zone", "general")),
        doc_type=DocType(meta.get("doc_type", "other")),
        language=str(meta.get("language", "es")),
        criticality=Criticality(meta.get("criticality", "medium")),
        version=str(meta.get("version", "1.0")),
        source_path=str(meta.get("source_path", "")),
        title=str(meta.get("title", "")),
        is_numbered_procedure=bool(meta.get("is_numbered_procedure", False)),
        has_safety_warning=bool(meta.get("has_safety_warning", False)),
    )


def rrf_fuse(
    lexical: list[tuple[Chunk, float, int]],
    dense: list[tuple[Chunk, float, int]],
    rrf_k: int = 60,
) -> list[ScoredChunk]:
    """Reciprocal Rank Fusion — robusto cuando las escalas de score no son comparables."""
    acc: dict[str, ScoredChunk] = {}
    for ch, score, rank in lexical:
        item = acc.get(ch.chunk_id)
        add = 1.0 / (rrf_k + rank)
        if item is None:
            acc[ch.chunk_id] = ScoredChunk(
                chunk=ch,
                score=add,
                lexical_rank=rank,
                rrf_score=add,
                selection_reason=f"bm25_rank={rank} bm25={score:.3f}",
            )
        else:
            item.score += add
            item.rrf_score = (item.rrf_score or 0) + add
            item.lexical_rank = rank
            item.selection_reason += f" | bm25_rank={rank} bm25={score:.3f}"

    for ch, score, rank in dense:
        item = acc.get(ch.chunk_id)
        add = 1.0 / (rrf_k + rank)
        if item is None:
            acc[ch.chunk_id] = ScoredChunk(
                chunk=ch,
                score=add,
                dense_rank=rank,
                rrf_score=add,
                selection_reason=f"dense_rank={rank} sim={score:.3f}",
            )
        else:
            item.score += add
            item.rrf_score = (item.rrf_score or 0) + add
            item.dense_rank = rank
            item.selection_reason += f" | dense_rank={rank} sim={score:.3f}"

    return sorted(acc.values(), key=lambda x: x.score, reverse=True)


class HybridRetriever:
    def __init__(self, index: HybridIndex, cfg: AppConfig | None = None) -> None:
        self.index = index
        self.cfg = cfg or load_config()

    def _where(self, filters: RetrievalFilters | None) -> dict[str, Any] | None:
        if not filters:
            return None
        clauses: list[dict[str, Any]] = []
        if filters.zones:
            clauses.append({"zone": {"$in": [z.value for z in filters.zones]}})
        if filters.doc_types:
            clauses.append({"doc_type": {"$in": [d.value for d in filters.doc_types]}})
        if filters.languages:
            clauses.append({"language": {"$in": list(filters.languages)}})
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def retrieve(
        self,
        query: str,
        zones: list[Zone | str],
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> tuple[list[ScoredChunk], dict[str, int]]:
        rc = self.cfg.retrieval
        lex_hits: list[tuple[Chunk, float, int]] = []
        dense_hits: list[tuple[Chunk, float, int]] = []

        zones_norm = [z if isinstance(z, Zone) else Zone(z) for z in zones]
        # Si hay varias zonas, buscamos en cada una y fusionamos
        for z in zones_norm:
            lex = self.index.lexical(z).search(
                query,
                top_k=rc.lexical_top_k,
                zones=filters.zones if filters else None,
                doc_types=filters.doc_types if filters else None,
                languages=filters.languages if filters else None,
                criticality_min=filters.criticality_min if filters else None,
            )
            # Ajustar ranks locales ya vienen 1..n; al fusionar RRF usamos esos ranks
            lex_hits.extend(lex)

            where = self._where(filters)
            dense_raw = self.index.dense(z).search(query, top_k=rc.dense_top_k, where=where)
            for cid, doc, meta, score, rank in dense_raw:
                ch = chunk_from_meta(cid, doc, meta)
                if filters and filters.criticality_min:
                    if CRIT_ORDER[ch.criticality] < CRIT_ORDER[filters.criticality_min]:
                        continue
                dense_hits.append((ch, score, rank))

        fused = rrf_fuse(lex_hits, dense_hits, rrf_k=rc.rrf_k)
        fused = [f for f in fused if (f.rrf_score or 0) >= rc.min_rrf_score]
        limit = top_k or (rc.rerank_top_k * 3)
        fused = fused[:limit]
        stats = {
            "candidates_lexical": len(lex_hits),
            "candidates_dense": len(dense_hits),
            "after_rrf": len(fused),
        }
        return fused, stats
