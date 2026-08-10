"""Router de consultas → mini-expertos por zona del buque.

Diseño deliberadamente conservador:
- Si la confianza es baja → expert general.
- Si hay señales de emergencia → forzar zona emergencias + modo extractivo.
- Permite multi-experto cuando la pregunta cruza dominios.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import ResponseMode, Zone

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


class QueryRouter:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self._zone_keywords = {
            z.id: [k.lower() for k in z.keywords] for z in self.cfg.zones
        }

    def _emergency(self, query: str) -> bool:
        q = query.lower()
        return any(re.search(p, q, re.I) for p in EMERGENCY_PATTERNS)

    def route(
        self,
        query: str,
        forced_zone: Zone | None = None,
        emergency: bool = False,
        mode: ResponseMode = ResponseMode.AUTO,
    ) -> RouteDecision:
        if forced_zone:
            is_em = emergency or forced_zone == Zone.EMERGENCIAS or self._emergency(query)
            sug = mode
            if mode == ResponseMode.AUTO and is_em:
                sug = ResponseMode.EXTRACTIVE
            return RouteDecision(
                zones=[forced_zone],
                confidence=1.0,
                reason=f"zona forzada por usuario: {forced_zone.value}",
                emergency=is_em,
                suggested_mode=sug,
            )

        q = query.lower()
        is_em = emergency or self._emergency(q)
        scores: dict[str, float] = {}
        hits: dict[str, list[str]] = {}
        for zid, kws in self._zone_keywords.items():
            matched = [k for k in kws if k in q]
            if matched:
                # score = cobertura de keywords + bonus por longitud de match
                scores[zid] = len(matched) + 0.1 * sum(len(m) for m in matched) / 10.0
                hits[zid] = matched

        if is_em:
            scores[Zone.EMERGENCIAS.value] = scores.get(Zone.EMERGENCIAS.value, 0) + 2.5
            hits.setdefault(Zone.EMERGENCIAS.value, []).append("<emergency_pattern>")

        if not scores:
            fb = Zone(self.cfg.router.fallback_zone)
            sug = ResponseMode.EXTRACTIVE if is_em else (
                mode if mode != ResponseMode.AUTO else ResponseMode.SEMI
            )
            return RouteDecision(
                zones=[fb],
                confidence=0.2,
                reason="sin señales de zona → fallback general",
                emergency=is_em,
                suggested_mode=sug,
            )

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        # Preferir zonas de dominio sobre 'general' a igualdad aproximada
        ranked.sort(
            key=lambda x: (x[1], 0 if x[0] != self.cfg.router.fallback_zone else -1),
            reverse=True,
        )
        best_id, best_score = ranked[0]
        confidence = min(1.0, best_score / 4.0)

        selected: list[Zone] = []
        if confidence < self.cfg.router.confidence_threshold:
            # Incluir best + fallback, pero si best es general y hay otra zona, priorizarla
            domain = [zid for zid, _ in ranked if zid != self.cfg.router.fallback_zone]
            if domain:
                selected = [Zone(domain[0]), Zone(self.cfg.router.fallback_zone)]
            else:
                selected = [Zone(self.cfg.router.fallback_zone)]
            reason = (
                f"confianza baja ({confidence:.2f}<{self.cfg.router.confidence_threshold}); "
                f"mejor={selected[0].value} hits={hits.get(selected[0].value, [])}"
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
                f"top={[z.value for z in selected]} "
                f"scores={{ {', '.join(f'{z.value}:{scores.get(z.value,0):.2f}' for z in selected)} }} "
                f"hits={{ { {z.value: hits.get(z.value, []) for z in selected} } }}"
            )

        # Dedup preserving order
        seen = set()
        zones: list[Zone] = []
        for z in selected:
            if z.value not in seen:
                zones.append(z)
                seen.add(z.value)

        if mode == ResponseMode.AUTO:
            if is_em or Zone.EMERGENCIAS in zones:
                sug_mode = ResponseMode.EXTRACTIVE
            else:
                sug_mode = ResponseMode.SEMI
        else:
            sug_mode = mode

        return RouteDecision(
            zones=zones or [Zone.GENERAL],
            confidence=confidence,
            reason=reason,
            emergency=is_em,
            suggested_mode=sug_mode,
        )
