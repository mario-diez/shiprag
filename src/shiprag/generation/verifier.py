"""Verificación de grounding y abstención.

La respuesta solo se emite si está soportada por los fragmentos recuperados.
Umbrales más altos en emergencias.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import (
    AnswerStatus,
    QueryResponse,
    ResponseMode,
    ScoredChunk,
)

TOKEN_RE = re.compile(r"[a-záéíóúñü0-9]{2,}", re.I)
CODE_RE = re.compile(r"\b[a-z]{1,4}-?\d{1,4}\b", re.I)
STOP = {
    "para", "como", "que", "los", "las", "del", "una", "por", "con", "the", "and",
    "esto", "esta", "este", "según", "segun", "nota", "debe", "sobre", "cual",
    "cuál", "cuando", "dónde", "donde", "hace", "hacer", "tiene", "el", "la",
    "de", "en", "un", "al", "se", "su", "no", "si",
}


def tokenize(text: str) -> set[str]:
    toks = {t for t in TOKEN_RE.findall(text.lower()) if t not in STOP and len(t) >= 2}
    # Preservar códigos técnicos completos (fo-12, p-255)
    for c in CODE_RE.findall(text.lower()):
        toks.add(c)
    return toks


def grounding_score(answer: str, evidence: list[ScoredChunk]) -> float:
    """Fracción de tokens de la respuesta (no boilerplate) presentes en la evidencia."""
    clean = re.sub(
        r"(Según «.*?»:|AVISOS DE SEGURIDAD.*|Pasos del procedimiento.*|"
        r"Evidencia adicional:|Nota:.*|MODO SOLO CITAS.*)",
        " ",
        answer,
        flags=re.I | re.S,
    )
    ans_toks = tokenize(clean)
    if not ans_toks:
        return 1.0 if evidence else 0.0
    ev_toks: set[str] = set()
    for it in evidence:
        ev_toks |= tokenize(it.chunk.text)
    if not ev_toks:
        return 0.0
    return len(ans_toks & ev_toks) / len(ans_toks)


def query_relevance(query: str, items: list[ScoredChunk], top_n: int = 3) -> float:
    """Overlap consulta↔evidencia. Evita responder con docs solo porque el índice no está vacío."""
    q = tokenize(query)
    if not q or not items:
        return 0.0
    best = 0.0
    for it in items[:top_n]:
        d = tokenize(it.chunk.text)
        if not d:
            continue
        best = max(best, len(q & d) / len(q))
    return best


def evidence_score(items: list[ScoredChunk]) -> float:
    if not items:
        return 0.0
    top = max(0.0, float(items[0].score))
    if top > 1.5:
        top = 1 / (1 + math.exp(-top / 4.0))
    return min(1.0, top)


@dataclass
class VerificationResult:
    accepted: bool
    response: QueryResponse
    grounding: float
    evidence: float


class AnswerVerifier:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()

    def verify(
        self,
        query: str,
        response: QueryResponse,
        items: list[ScoredChunk],
        *,
        emergency: bool,
        mode: ResponseMode,
    ) -> VerificationResult:
        g = grounding_score(response.answer, items)
        e = evidence_score(items)
        qrel = query_relevance(query, items)
        g_th = (
            self.cfg.generation.emergency_grounding_threshold
            if emergency
            else self.cfg.generation.grounding_threshold
        )
        e_th = (
            self.cfg.retrieval.emergency_min_evidence_score
            if emergency
            else self.cfg.retrieval.min_evidence_score
        )
        # Relevancia mínima consulta-documento (más permisiva en emergencia
        # porque el router ya filtró, pero aún así exigimos algo).
        q_th = 0.22 if emergency else 0.28

        response.confidence = round(0.45 * e + 0.25 * g + 0.30 * qrel, 4)

        weak = (not items) or (e < e_th) or (qrel < q_th)
        if mode == ResponseMode.CITATIONS_ONLY and not weak and response.citations:
            return VerificationResult(True, response, g, e)

        if weak:
            abstain = QueryResponse(
                status=AnswerStatus.NOT_FOUND,
                answer=(
                    "NO TENGO SUFICIENTE BASE DOCUMENTAL para responder con seguridad. "
                    "No se ha recuperado evidencia bastante alineada con la consulta. "
                    "Consulte el manual oficial en papel o reformule con más contexto "
                    "(sistema, ubicación, código de procedimiento)."
                ),
                citations=response.citations[:3] if qrel >= 0.15 else [],
                confidence=response.confidence,
                mode_used=mode,
                clarification_question=(
                    "¿Puede indicar la zona del buque, el sistema afectado "
                    "o el código del procedimiento?"
                    if self.cfg.generation.clarify_on_low_confidence
                    else None
                ),
            )
            return VerificationResult(False, abstain, g, e)

        if g < g_th and mode == ResponseMode.GENERATIVE:
            abstain = QueryResponse(
                status=AnswerStatus.ABSTAIN,
                answer=(
                    "ME ABSTENGO: la respuesta generada no está suficientemente anclada "
                    "en las fuentes recuperadas. Use el modo extractivo/citas o revise "
                    "los fragmentos manualmente."
                ),
                citations=response.citations,
                confidence=response.confidence,
                mode_used=mode,
            )
            return VerificationResult(False, abstain, g, e)

        if emergency and not any(
            c.is_numbered_procedure or c.has_safety_warning
            for c in (it.chunk for it in items[:5])
        ):
            if self.cfg.generation.clarify_on_low_confidence:
                response.status = AnswerStatus.CLARIFY
                response.clarification_question = (
                    "Consulta crítica: no localicé un procedimiento numerado claro. "
                    "¿Busca un procedimiento de emergencia concreto (incendio, MOB, "
                    "abandono, SOPEP, blackout)?"
                )
                response.answer = (
                    response.answer
                    + "\n\n[AVISO] Evidencia no estructurada como procedimiento oficial numerado."
                )

        return VerificationResult(True, response, g, e)
