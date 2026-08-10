"""Mini-experto por zona: encapsula índice + reglas de respuesta."""

from __future__ import annotations

from dataclasses import dataclass

from shiprag.core.config import AppConfig, ZoneConfig, load_config
from shiprag.core.schemas import ResponseMode, Zone
from shiprag.index.store import HybridIndex
from shiprag.retrieval.hybrid import HybridRetriever
from shiprag.retrieval.reranker import Reranker, build_reranker


@dataclass
class ExpertProfile:
    zone: Zone
    label: str
    default_mode: ResponseMode
    criticality_bias: bool


class ZoneExpert:
    def __init__(
        self,
        zone: Zone,
        index: HybridIndex,
        cfg: AppConfig | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.zone = zone
        self.index = index
        self.retriever = HybridRetriever(index, self.cfg)
        zcfg: ZoneConfig | None = self.cfg.zone_map().get(zone.value)
        default_mode = ResponseMode.SEMI
        critical = False
        label = zone.value
        if zcfg:
            label = zcfg.label
            if zcfg.default_response_mode:
                default_mode = ResponseMode(zcfg.default_response_mode)
            critical = (zcfg.criticality or "") == "critical"
        if zone == Zone.EMERGENCIAS:
            default_mode = ResponseMode.EXTRACTIVE
            critical = True
        self.profile = ExpertProfile(
            zone=zone,
            label=label,
            default_mode=default_mode,
            criticality_bias=critical,
        )
        self.reranker = reranker or build_reranker(self.cfg, emergency_bias=critical)

    def retrieve(self, query: str, filters=None, top_k: int | None = None):
        items, stats = self.retriever.retrieve(
            query,
            zones=[self.zone],
            filters=filters,
            top_k=top_k or (self.cfg.retrieval.rerank_top_k * 3),
        )
        reranked = self.reranker.rerank(query, items, top_k or self.cfg.retrieval.rerank_top_k)
        return reranked, stats
