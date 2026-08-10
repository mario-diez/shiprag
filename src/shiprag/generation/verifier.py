"""Verificación de grounding y abstención.

Capa 1: scoring léxico (token overlap) entre evidencia y respuesta.
Capa 2 (opcional): entailment NLI offline; si está desactivado o falla la carga,
se mantiene solo el comportamiento léxico.
Umbrales más altos en emergencias.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import (
    AnswerStatus,
    QueryResponse,
    ResponseMode,
    ScoredChunk,
)

logger = logging.getLogger("shiprag.verifier")

TOKEN_RE = re.compile(r"[a-záéíóúñü0-9]{2,}", re.I)
CODE_RE = re.compile(r"\b[a-z]{1,4}-?\d{1,4}\b", re.I)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
STOP = {
    "para", "como", "que", "los", "las", "del", "una", "por", "con", "the", "and",
    "esto", "esta", "este", "según", "segun", "nota", "debe", "sobre", "cual",
    "cuál", "cuando", "dónde", "donde", "hace", "hacer", "tiene", "el", "la",
    "de", "en", "un", "al", "se", "su", "no", "si",
}


def tokenize(text: str) -> set[str]:
    toks = {t for t in TOKEN_RE.findall(text.lower()) if t not in STOP and len(t) >= 2}
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


def _clean_answer_for_nli(answer: str) -> str:
    return re.sub(
        r"(Según «.*?»:|AVISOS DE SEGURIDAD.*|Pasos del procedimiento.*|"
        r"Evidencia adicional:|Nota:.*|MODO SOLO CITAS.*|\[AVISO\].*)",
        " ",
        answer,
        flags=re.I | re.S,
    ).strip()


def _answer_sentences(answer: str) -> list[str]:
    clean = _clean_answer_for_nli(answer)
    parts = [p.strip() for p in SENT_SPLIT_RE.split(clean) if p and p.strip()]
    # Filtrar ruido muy corto
    return [p for p in parts if len(tokenize(p)) >= 3] or ([clean] if clean else [])


def _evidence_premise(items: list[ScoredChunk], max_chunks: int = 4, max_chars: int = 3500) -> str:
    parts: list[str] = []
    total = 0
    for it in items[:max_chunks]:
        text = (it.chunk.text or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            text = text[: max(0, max_chars - total)]
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(parts)


@dataclass
class VerificationResult:
    accepted: bool
    response: QueryResponse
    grounding: float
    evidence: float
    nli_score: float | None = None


class NLIEntailmentChecker:
    """Clasificador NLI local (premise=evidencia, hypothesis=afirmación)."""

    def __init__(self, name_or_path: str, device: str = "cpu") -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._device = device
        self._name = name_or_path
        self._tokenizer = AutoTokenizer.from_pretrained(name_or_path)
        self._model = AutoModelForSequenceClassification.from_pretrained(name_or_path)
        self._model.to(device)
        self._model.eval()
        self._torch = torch
        id2label = {int(k): str(v).lower() for k, v in self._model.config.id2label.items()}
        self._entail_idx = next(
            (i for i, lab in id2label.items() if "entail" in lab),
            2 if len(id2label) >= 3 else 0,
        )
        self._contra_idx = next(
            (i for i, lab in id2label.items() if "contradict" in lab),
            0,
        )

    @property
    def name(self) -> str:
        return self._name

    def entailment_scores(self, premise: str, hypotheses: list[str]) -> list[float]:
        if not premise or not hypotheses:
            return []
        scores: list[float] = []
        torch = self._torch
        with torch.no_grad():
            for hyp in hypotheses:
                inputs = self._tokenizer(
                    premise,
                    hyp,
                    return_tensors="pt",
                    truncation=True,
                    max_length=512,
                )
                inputs = {k: v.to(self._device) for k, v in inputs.items()}
                logits = self._model(**inputs).logits[0]
                probs = torch.softmax(logits, dim=-1)
                # Penalizar contradicción fuerte
                entail = float(probs[self._entail_idx].item())
                contra = float(probs[self._contra_idx].item()) if self._contra_idx is not None else 0.0
                if contra > 0.5:
                    scores.append(0.0)
                else:
                    scores.append(entail)
        return scores


class AnswerVerifier:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        nli: NLIEntailmentChecker | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        self.nli = nli

    def _nli_ok(
        self,
        response: QueryResponse,
        items: list[ScoredChunk],
        *,
        emergency: bool,
    ) -> tuple[bool, float | None]:
        if self.nli is None:
            return True, None
        premise = _evidence_premise(items)
        hyps = _answer_sentences(response.answer)
        if not premise or not hyps:
            return True, None
        scores = self.nli.entailment_scores(premise, hyps)
        if not scores:
            return True, None
        # Todas las afirmaciones deben superar el umbral (min)
        score = min(scores)
        th = (
            self.cfg.models.verifier.emergency_entailment_threshold
            if emergency
            else self.cfg.models.verifier.entailment_threshold
        )
        return score >= th, score

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
        q_th = 0.22 if emergency else 0.28

        response.confidence = round(0.45 * e + 0.25 * g + 0.30 * qrel, 4)

        weak = (not items) or (e < e_th) or (qrel < q_th)
        if mode == ResponseMode.CITATIONS_ONLY and not weak and response.citations:
            nli_ok, nli_score = self._nli_ok(response, items, emergency=emergency)
            if not nli_ok:
                return self._nli_abstain(response, mode, g, e, nli_score)
            return VerificationResult(True, response, g, e, nli_score)

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

        # Capa NLI: solo si la respuesta iba a aceptarse léxicamente
        nli_ok, nli_score = self._nli_ok(response, items, emergency=emergency)
        if not nli_ok:
            return self._nli_abstain(response, mode, g, e, nli_score)

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

        return VerificationResult(True, response, g, e, nli_score)

    def _nli_abstain(
        self,
        response: QueryResponse,
        mode: ResponseMode,
        g: float,
        e: float,
        nli_score: float | None,
    ) -> VerificationResult:
        abstain = QueryResponse(
            status=AnswerStatus.ABSTAIN,
            answer=(
                "ME ABSTENGO: el verificador NLI no confirma que la respuesta esté "
                "implicada por la evidencia recuperada (entailment insuficiente). "
                "Revise las citas o use modo extractivo/citas."
            ),
            citations=response.citations,
            confidence=response.confidence,
            mode_used=mode,
        )
        return VerificationResult(False, abstain, g, e, nli_score)


def _resolve_verifier_path(cfg: AppConfig, name_or_path: str | None) -> str | None:
    if not name_or_path:
        return None
    path = cfg.resolve(name_or_path)
    if path.exists():
        return str(path)
    return name_or_path if Path(name_or_path).exists() else None


def build_verifier(cfg: AppConfig | None = None) -> AnswerVerifier:
    """Factory espejo de build_reranker: lexical | auto/nli con fallback léxico."""
    cfg = cfg or load_config()
    backend = (cfg.models.verifier.backend or "lexical").lower().strip()
    if backend == "lexical":
        logger.info("Verifier: solo léxico (backend=lexical)")
        return AnswerVerifier(cfg)

    candidate = _resolve_verifier_path(cfg, cfg.models.verifier.name_or_path)
    if backend in {"auto", "nli"} and candidate:
        try:
            logger.info("Cargando verifier NLI desde %s", candidate)
            nli = NLIEntailmentChecker(candidate, device=cfg.models.verifier.device)
            return AnswerVerifier(cfg, nli=nli)
        except Exception as exc:
            logger.warning("Verifier NLI falló (%s). Fallback léxico.", exc)

    logger.info(
        "Verifier: solo léxico (profile=%s backend=%s)",
        cfg.profile.id,
        backend,
    )
    return AnswerVerifier(cfg)
