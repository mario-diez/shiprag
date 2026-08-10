"""Router de consultas → mini-expertos por zona del buque.

Diseño deliberadamente conservador:
- Patrones de emergencia → fuerza zona emergencias + modo extractivo.
- Con embeddings reales: clasificación por similitud coseno vs frases de ejemplo.
- Sin embeddings (lite / hash): fallback a keywords.
- Si la confianza es baja → expert general.
- Permite multi-experto cuando la pregunta cruza dominios.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import ResponseMode, Zone
from shiprag.index.embeddings import EmbeddingBackend, HashEmbedding

logger = logging.getLogger("shiprag.router")

EMERGENCY_PATTERNS = [
    r"hombre\s+al\s+agua",
    r"man\s+overboard",
    r"\bmayday\b",
    r"\bdistress\b",
    r"abandono\s+del?\s+buque",
    r"\bsopep\b",
    r"incendio\s+en",
    r"\bblackout\b",
    r"inundaci[oó]n",
    r"colisi[oó]n",
    r"varada",
    r"emergencia",
]


@dataclass
class RouteDecision:
    zones: list[Zone]
    confidence: float
    reason: str
    emergency: bool
    suggested_mode: ResponseMode


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class QueryRouter:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        embedder: EmbeddingBackend | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.embedder = embedder
        self._zone_keywords = {
            z.id: [k.lower() for k in z.keywords] for z in self.cfg.zones
        }
        self._zone_examples = {
            z.id: list(z.examples) if z.examples else list(z.keywords)
            for z in self.cfg.zones
        }
        self._prototypes: dict[str, np.ndarray] | None = None

    def _emergency(self, query: str) -> bool:
        q = query.lower()
        return any(re.search(p, q, re.I) for p in EMERGENCY_PATTERNS)

    def _use_embedding(self) -> bool:
        backend = (self.cfg.router.backend or "auto").lower().strip()
        if backend == "keywords":
            return False
        if backend == "embedding":
            return self.embedder is not None and not isinstance(self.embedder, HashEmbedding)
        # auto
        if self.embedder is None or isinstance(self.embedder, HashEmbedding):
            return False
        emb_backend = (self.cfg.models.embedding.backend or "").lower().strip()
        return emb_backend not in {"hash", ""}

    def _ensure_prototypes(self) -> dict[str, np.ndarray]:
        if self._prototypes is not None:
            return self._prototypes
        assert self.embedder is not None
        protos: dict[str, np.ndarray] = {}
        for zid, phrases in self._zone_examples.items():
            texts = [p for p in phrases if p and str(p).strip()]
            if not texts:
                continue
            embs = self.embedder.embed_documents(texts)
            proto = np.mean(np.asarray(embs, dtype=np.float32), axis=0)
            n = float(np.linalg.norm(proto))
            if n > 0:
                proto = proto / n
            protos[zid] = proto
        self._prototypes = protos
        return protos

    def _score_keywords(self, q: str) -> tuple[dict[str, float], dict[str, list[str]]]:
        scores: dict[str, float] = {}
        hits: dict[str, list[str]] = {}
        for zid, kws in self._zone_keywords.items():
            matched = [k for k in kws if k in q]
            if matched:
                scores[zid] = len(matched) + 0.1 * sum(len(m) for m in matched) / 10.0
                hits[zid] = matched
        return scores, hits

    def _score_embedding(self, query: str) -> tuple[dict[str, float], dict[str, list[str]]]:
        protos = self._ensure_prototypes()
        q_emb = self.embedder.embed_query(query)  # type: ignore[union-attr]
        scores: dict[str, float] = {}
        hits: dict[str, list[str]] = {}
        for zid, proto in protos.items():
            sim = _cosine(q_emb, proto)
            # Escala similar a keyword scores (~0–4) para reutilizar umbrales
            scores[zid] = max(0.0, sim) * 4.0
            hits[zid] = [f"<emb_sim={sim:.3f}>"]
        return scores, hits

    def _suggest_mode(self, mode: ResponseMode, is_em: bool, zones: list[Zone]) -> ResponseMode:
        if mode == ResponseMode.AUTO:
            if is_em or Zone.EMERGENCIAS in zones:
                return ResponseMode.EXTRACTIVE
            return ResponseMode.SEMI
        return mode

    def route(
        self,
        query: str,
        forced_zone: Zone | None = None,
        emergency: bool = False,
        mode: ResponseMode = ResponseMode.AUTO,
    ) -> RouteDecision:
        is_em = emergency or self._emergency(query)
        if forced_zone:
            is_em = is_em or forced_zone == Zone.EMERGENCIAS
            return RouteDecision(
                zones=[forced_zone],
                confidence=1.0,
                reason=f"zona forzada por usuario: {forced_zone.value}",
                emergency=is_em,
                suggested_mode=self._suggest_mode(mode, is_em, [forced_zone]),
            )

        # Override duro: patrón / flag de emergencia → solo emergencias + extractivo
        if is_em:
            zones = [Zone.EMERGENCIAS]
            return RouteDecision(
                zones=zones,
                confidence=1.0,
                reason="override emergencia → zona emergencias + extractivo",
                emergency=True,
                suggested_mode=self._suggest_mode(mode, True, zones),
            )

        q = query.lower()
        used_emb = False
        if self._use_embedding():
            try:
                scores, hits = self._score_embedding(query)
                used_emb = True
            except Exception as exc:
                logger.warning("Router embedding falló (%s). Fallback keywords.", exc)
                scores, hits = self._score_keywords(q)
        else:
            scores, hits = self._score_keywords(q)

        if not scores or max(scores.values(), default=0) <= 0:
            fb = Zone(self.cfg.router.fallback_zone)
            return RouteDecision(
                zones=[fb],
                confidence=0.2,
                reason="sin señales de zona → fallback general",
                emergency=False,
                suggested_mode=self._suggest_mode(mode, False, [fb]),
            )

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ranked.sort(
            key=lambda x: (x[1], 0 if x[0] != self.cfg.router.fallback_zone else -1),
            reverse=True,
        )
        best_id, best_score = ranked[0]
        confidence = min(1.0, best_score / 4.0)

        selected: list[Zone] = []
        if confidence < self.cfg.router.confidence_threshold:
            domain = [zid for zid, _ in ranked if zid != self.cfg.router.fallback_zone]
            if domain:
                selected = [Zone(domain[0]), Zone(self.cfg.router.fallback_zone)]
            else:
                selected = [Zone(self.cfg.router.fallback_zone)]
            reason = (
                f"confianza baja ({confidence:.2f}<{self.cfg.router.confidence_threshold}); "
                f"mejor={selected[0].value} method={'emb' if used_emb else 'kw'} "
                f"hits={hits.get(selected[0].value, [])}"
            )
        else:
            selected = [Zone(best_id)]
            if self.cfg.router.allow_multi_expert:
                for zid, sc in ranked[1 : self.cfg.router.max_experts]:
                    if zid == self.cfg.router.fallback_zone:
                        continue
                    if sc >= best_score * 0.55:
                        selected.append(Zone(zid))
            reason = (
                f"top={[z.value for z in selected]} method={'emb' if used_emb else 'kw'} "
                f"scores={{ {', '.join(f'{z.value}:{scores.get(z.value,0):.2f}' for z in selected)} }} "
                f"hits={{ { {z.value: hits.get(z.value, []) for z in selected} } }}"
            )

        seen: set[str] = set()
        zones: list[Zone] = []
        for z in selected:
            if z.value not in seen:
                zones.append(z)
                seen.add(z.value)

        return RouteDecision(
            zones=zones or [Zone.GENERAL],
            confidence=confidence,
            reason=reason,
            emergency=False,
            suggested_mode=self._suggest_mode(mode, False, zones),
        )
