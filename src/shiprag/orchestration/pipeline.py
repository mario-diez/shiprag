"""Orquestador central: router → expertos → retrieve → rerank → generate → verify."""

from __future__ import annotations

import logging
from typing import Any

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config
from shiprag.core.logging import write_trace
from shiprag.core.schemas import (
    QueryRequest,
    QueryResponse,
    ResponseMode,
    RetrievalFilters,
    RetrievalTrace,
    ScoredChunk,
    Zone,
)
from shiprag.experts.expert import ZoneExpert
from shiprag.experts.router import QueryRouter
from shiprag.generation.generator import ExtractiveGenerator, build_generator
from shiprag.generation.verifier import build_verifier
from shiprag.index.store import HybridIndex
from shiprag.retrieval.reranker import LexicalReranker

logger = logging.getLogger("shiprag.orchestrator")


class Orchestrator:
    def __init__(self, cfg: AppConfig | None = None, index: HybridIndex | None = None) -> None:
        self.cfg = cfg or load_config()
        ensure_runtime_dirs(self.cfg)
        self.index = index or HybridIndex(self.cfg)
        self.router = QueryRouter(self.cfg, embedder=self.index.embedder)
        self.generator = build_generator(self.cfg)
        self.extractive = ExtractiveGenerator()
        self.verifier = build_verifier(self.cfg)
        self._experts: dict[str, ZoneExpert] = {}

    def expert(self, zone: Zone) -> ZoneExpert:
        if zone.value not in self._experts:
            self._experts[zone.value] = ZoneExpert(zone, self.index, self.cfg)
        return self._experts[zone.value]

    def _merge_results(self, buckets: list[list[ScoredChunk]], top_k: int) -> list[ScoredChunk]:
        by_id: dict[str, ScoredChunk] = {}
        for bucket in buckets:
            for it in bucket:
                prev = by_id.get(it.chunk.chunk_id)
                if prev is None or it.score > prev.score:
                    by_id[it.chunk.chunk_id] = it
        merged = sorted(by_id.values(), key=lambda x: x.score, reverse=True)
        return merged[:top_k]

    def query(self, req: QueryRequest) -> QueryResponse:
        decision = self.router.route(
            req.query,
            forced_zone=req.zone,
            emergency=req.emergency,
            mode=req.mode,
        )
        mode = decision.suggested_mode
        if req.mode == ResponseMode.CITATIONS_ONLY:
            mode = ResponseMode.CITATIONS_ONLY
        elif req.mode != ResponseMode.AUTO:
            mode = req.mode
        if decision.emergency and mode == ResponseMode.GENERATIVE:
            # Seguridad: no generación libre en emergencia
            mode = ResponseMode.EXTRACTIVE

        # Si hay LLM local activo y no es emergencia, preferir generación anclada
        llm_ready = bool(self.cfg.models.llm.enabled) and not isinstance(
            self.generator, ExtractiveGenerator
        )
        if (
            llm_ready
            and not decision.emergency
            and mode in {ResponseMode.AUTO, ResponseMode.SEMI, ResponseMode.GENERATIVE}
            and req.mode != ResponseMode.EXTRACTIVE
            and req.mode != ResponseMode.CITATIONS_ONLY
        ):
            mode = ResponseMode.GENERATIVE

        filters = req.filters or RetrievalFilters()
        if decision.emergency and filters.criticality_min is None:
            # No forzar critical-only (podría vaciar resultados); prioriza el reranker
            pass

        top_k = req.top_k or self.cfg.retrieval.rerank_top_k
        # Si hay expertos de dominio, no consultar 'general' (espejo de todo el corpus):
        # contaminaría el ranking con documentos de otras zonas.
        zones = list(decision.zones)
        domain = [z for z in zones if z != Zone.GENERAL]
        if domain:
            zones = domain

        buckets: list[list[ScoredChunk]] = []
        stats_acc = {"candidates_lexical": 0, "candidates_dense": 0, "after_rrf": 0}
        for z in zones:
            items, stats = self.expert(z).retrieve(req.query, filters=filters, top_k=top_k * 2)
            buckets.append(items)
            for k in stats_acc:
                stats_acc[k] += stats.get(k, 0)

        # Si multi-experto, re-fusionar con bias de emergencia
        merged = self._merge_results(buckets, top_k=top_k * 2)
        if decision.emergency:
            merged = LexicalReranker(emergency_bias=True).rerank(req.query, merged, top_k)
        else:
            merged = merged[:top_k]

        # Generación
        if mode in {ResponseMode.EXTRACTIVE, ResponseMode.CITATIONS_ONLY, ResponseMode.SEMI}:
            response = self.extractive.generate(
                req.query, merged, mode, max_citations=self.cfg.generation.max_citations
            )
        else:
            response = self.generator.generate(
                req.query, merged, mode, max_citations=self.cfg.generation.max_citations
            )
        verified = self.verifier.verify(
            req.query, response, merged, emergency=decision.emergency, mode=mode
        )
        final = verified.response
        final.mode_used = mode
        final.zones_used = [z.value for z in zones]

        trace = RetrievalTrace(
            routed_zones=[z.value for z in zones],
            router_confidence=decision.confidence,
            router_reason=decision.reason,
            mode=mode,
            filters_applied=filters.model_dump(mode="json") if filters else {},
            candidates_lexical=stats_acc["candidates_lexical"],
            candidates_dense=stats_acc["candidates_dense"],
            after_rrf=stats_acc["after_rrf"],
            after_rerank=len(merged),
            selected=[
                {
                    "chunk_id": it.chunk.chunk_id,
                    "doc_id": it.chunk.doc_id,
                    "page": it.chunk.page_start,
                    "section": it.chunk.section,
                    "score": it.score,
                    "reason": it.selection_reason,
                    "preview": it.chunk.text[:240],
                }
                for it in merged
            ],
            evidence_score=verified.evidence,
            grounding_score=verified.grounding,
            abstain_reason=None
            if verified.accepted
            else final.status.value,
        )
        final.trace = trace

        if self.cfg.logging.retrieval_trace:
            payload: dict[str, Any] = {
                "query": req.query,
                "response_status": final.status.value,
                "answer_preview": final.answer[:500],
                "trace": trace.model_dump(mode="json"),
                "citations": [c.model_dump(mode="json") for c in final.citations],
            }
            path = write_trace(payload, self.cfg)
            logger.info("Trace guardado en %s", path)

        return final
