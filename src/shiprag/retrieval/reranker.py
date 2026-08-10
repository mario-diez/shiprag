"""Reranking local: cross-encoder si hay modelo, si no lexical overlap."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import ScoredChunk

logger = logging.getLogger("shiprag.rerank")

TOKEN_RE = re.compile(r"[a-záéíóúñü0-9]+", re.I)
CODE_RE = re.compile(r"\b[A-Z]{1,4}-?\d{1,4}\b", re.I)


def _tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


class Reranker:
    def rerank(self, query: str, items: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError


class LexicalReranker(Reranker):
    """Overlap de tokens + bonus por códigos técnicos / procedimientos / warnings."""

    def __init__(self, emergency_bias: bool = False) -> None:
        self.emergency_bias = emergency_bias

    @property
    def name(self) -> str:
        return "lexical-overlap"

    def rerank(self, query: str, items: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        q = _tokens(query)
        codes = {c.lower() for c in CODE_RE.findall(query)}
        scored: list[ScoredChunk] = []
        for it in items:
            blob = f"{it.chunk.title} {it.chunk.section or ''} {it.chunk.text}"
            text_low = blob.lower()
            d = _tokens(blob)
            if not q or not d:
                overlap = 0.0
            else:
                overlap = len(q & d) / len(q)
            bonus = 0.0
            if codes:
                hits = sum(1 for c in codes if c in text_low)
                bonus += 0.35 * hits
            # Bonus título: términos de la query en el título pesan más
            title_toks = _tokens(it.chunk.title)
            if q and title_toks:
                bonus += 0.15 * (len(q & title_toks) / len(q))
            if self.emergency_bias:
                if it.chunk.is_numbered_procedure:
                    bonus += 0.08
                if it.chunk.has_safety_warning:
                    bonus += 0.05
            final = 0.55 * overlap + 0.25 * float(it.rrf_score or it.score) + bonus
            scored.append(
                it.model_copy(
                    update={
                        "score": final,
                        "rerank_score": final,
                        "selection_reason": it.selection_reason
                        + f" | rerank_lex={overlap:.3f} code_bonus={bonus:.2f} final={final:.3f}",
                    }
                )
            )
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


class CrossEncoderReranker(Reranker):
    def __init__(self, name_or_path: str, device: str = "cpu") -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(name_or_path, device=device)
        self._name = name_or_path

    @property
    def name(self) -> str:
        return self._name

    def rerank(self, query: str, items: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        if not items:
            return []
        pairs = [(query, it.chunk.text[:2000]) for it in items]
        scores = self._model.predict(pairs)
        out: list[ScoredChunk] = []
        for it, sc in zip(items, scores):
            final = float(sc)
            out.append(
                it.model_copy(
                    update={
                        "score": final,
                        "rerank_score": final,
                        "selection_reason": it.selection_reason
                        + f" | rerank_ce={final:.3f}",
                    }
                )
            )
        out.sort(key=lambda x: x.score, reverse=True)
        return out[:top_k]


def _resolve_reranker_candidate(cfg: AppConfig, name_or_path: str | None) -> str | None:
    if not name_or_path:
        return None
    path = cfg.resolve(name_or_path)
    if path.exists():
        return str(path)
    return name_or_path if Path(name_or_path).exists() else None


def build_reranker(cfg: AppConfig | None = None, emergency_bias: bool = False) -> Reranker:
    cfg = cfg or load_config()
    backend = (cfg.models.reranker.backend or "auto").lower().strip()

    if backend == "lexical":
        logger.info("Perfil fuerza LexicalReranker (backend=lexical)")
        return LexicalReranker(emergency_bias=emergency_bias)

    candidates: list[str] = []
    primary = _resolve_reranker_candidate(cfg, cfg.models.reranker.name_or_path)
    if primary:
        candidates.append(primary)
    fb = _resolve_reranker_candidate(cfg, cfg.models.reranker.fallback_name_or_path)
    if fb and fb not in candidates:
        candidates.append(fb)

    if backend in {"auto", "cross_encoder", "ce"}:
        for candidate in candidates:
            try:
                logger.info("Cargando reranker %s", candidate)
                return CrossEncoderReranker(candidate, device=cfg.models.reranker.device)
            except Exception as exc:
                logger.warning("Reranker CE falló en %s (%s).", candidate, exc)

    logger.info(
        "Usando LexicalReranker (profile=%s backend=%s)",
        cfg.profile.id,
        backend,
    )
    return LexicalReranker(emergency_bias=emergency_bias)
