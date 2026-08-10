"""Generación controlada: extractiva / semi-extractiva / LLM local opcional.

DECISIÓN DE SEGURIDAD:
En modo emergencia o citations_only NUNCA inventamos pasos.
Devolvemos citas literales (o casi) del procedimiento oficial.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import (
    AnswerStatus,
    Citation,
    ConflictInfo,
    QueryResponse,
    ResponseMode,
    ScoredChunk,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("shiprag.generation")

STEP_RE = re.compile(r"(?m)^\s*(?:\d+[\.\)]\s+.+)$")


def _clip_quote(text: str, max_len: int = 500) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rsplit(" ", 1)[0] + "…"


def to_citations(items: list[ScoredChunk], limit: int) -> list[Citation]:
    cites: list[Citation] = []
    for it in items[:limit]:
        cites.append(
            Citation(
                chunk_id=it.chunk.chunk_id,
                doc_id=it.chunk.doc_id,
                title=it.chunk.title,
                version=it.chunk.version,
                page_start=it.chunk.page_start,
                page_end=it.chunk.page_end,
                section=it.chunk.section,
                quote=_clip_quote(it.chunk.text),
                score=float(it.score),
                source_path=it.chunk.source_path,
            )
        )
    return cites


def detect_conflicts(items: list[ScoredChunk]) -> list[ConflictInfo]:
    """Conflicto solo si dos docs distintos tratan el MISMO tema con pasos divergentes.

    Evita falsos positivos al recuperar procedimientos de dominios distintos
    (p.ej. MOB + blackout) en la misma consulta amplia.
    """
    procs = [i for i in items if i.chunk.is_numbered_procedure]
    if len(procs) < 2:
        return []
    by_doc: dict[str, ScoredChunk] = {}
    for p in procs:
        by_doc.setdefault(p.chunk.doc_id, p)
    if len(by_doc) < 2:
        return []
    docs = list(by_doc.values())
    a, b = docs[0], docs[1]
    # Requieren solapamiento temático fuerte (título) antes de marcar conflicto
    title_a = set(re.findall(r"[a-záéíóúñü0-9]{4,}", a.chunk.title.lower()))
    title_b = set(re.findall(r"[a-záéíóúñü0-9]{4,}", b.chunk.title.lower()))
    # Quitar tokens genéricos de título
    generic = {"procedimiento", "manual", "checklist", "emergencias", "documento"}
    title_a -= generic
    title_b -= generic
    topic_overlap = len(title_a & title_b) / max(1, min(len(title_a), len(title_b))) if title_a and title_b else 0.0
    if topic_overlap < 0.4:
        return []
    steps_a = set(STEP_RE.findall(a.chunk.text))
    steps_b = set(STEP_RE.findall(b.chunk.text))
    if not steps_a or not steps_b:
        return []
    inter = steps_a & steps_b
    if len(inter) / max(1, min(len(steps_a), len(steps_b))) < 0.34:
        cites = to_citations([a, b], 2)
        return [
            ConflictInfo(
                topic=a.chunk.section or a.chunk.title or "procedimiento",
                sources=cites,
                detail=(
                    "Se encontraron procedimientos numerados distintos en documentos diferentes "
                    "sobre un tema similar. Revise cuál es la versión vigente a bordo antes de actuar."
                ),
            )
        ]
    return []


class ExtractiveGenerator:
    """Respuesta basada en extractos literales — modo preferido en emergencias."""

    def generate(
        self,
        query: str,
        items: list[ScoredChunk],
        mode: ResponseMode,
        max_citations: int = 5,
    ) -> QueryResponse:
        if not items:
            return QueryResponse(
                status=AnswerStatus.NOT_FOUND,
                answer=(
                    "NO ENCONTRADO: no hay fragmentos indexados relevantes para esta consulta. "
                    "No se debe improvisar un procedimiento."
                ),
                mode_used=mode,
                confidence=0.0,
            )

        cites = to_citations(items, max_citations)
        conflicts = detect_conflicts(items)

        if mode == ResponseMode.CITATIONS_ONLY:
            lines = ["MODO SOLO CITAS — extractos literales de la documentación a bordo:\n"]
            for i, c in enumerate(cites, 1):
                loc = f"{c.title} v{c.version}, pág. {c.page_start}"
                if c.section:
                    loc += f", § {c.section}"
                lines.append(f"[{i}] {loc}\n{c.quote}\n")
            status = AnswerStatus.CONFLICT if conflicts else AnswerStatus.OK
            return QueryResponse(
                status=status,
                answer="\n".join(lines),
                citations=cites,
                conflicts=conflicts,
                confidence=float(items[0].score),
                mode_used=mode,
            )

        # Extractivo / semi: priorizar match de códigos, overlap con query y procedimientos
        import re as _re

        from shiprag.generation.verifier import tokenize as _tok

        q_codes = {c.lower() for c in _re.findall(r"\b[A-Z]{1,4}-?\d{1,4}\b", query, flags=_re.I)}
        q_toks = _tok(query)

        def _rank_key(x: ScoredChunk):
            blob = f"{x.chunk.title} {x.chunk.section or ''} {x.chunk.text[:900]}"
            low = blob.lower()
            code_hit = 0
            if q_codes:
                code_hit = -sum(1 for c in q_codes if c in low)
            overlap = 0.0
            if q_toks:
                overlap = len(q_toks & _tok(blob)) / len(q_toks)
            return (
                code_hit,
                -overlap,
                0 if x.chunk.is_numbered_procedure else 1,
                0 if x.chunk.has_safety_warning else 1,
                -x.score,
            )

        ordered = sorted(items, key=_rank_key)
        primary = ordered[0]
        warnings = [it for it in ordered if it.chunk.has_safety_warning][:2]
        steps = STEP_RE.findall(primary.chunk.text)

        parts: list[str] = []
        parts.append(
            f"Según «{primary.chunk.title}» v{primary.chunk.version}, "
            f"páginas {primary.chunk.page_start}"
            + (
                f"–{primary.chunk.page_end}"
                if primary.chunk.page_end != primary.chunk.page_start
                else ""
            )
            + (f", sección «{primary.chunk.section}»" if primary.chunk.section else "")
            + ":"
        )
        if warnings and warnings[0].chunk.chunk_id != primary.chunk.chunk_id:
            parts.append("\nAVISOS DE SEGURIDAD (citados):")
            parts.append(_clip_quote(warnings[0].chunk.text, 400))

        if steps and mode in {ResponseMode.EXTRACTIVE, ResponseMode.SEMI, ResponseMode.AUTO}:
            parts.append("\nPasos del procedimiento (extracto):")
            for s in steps[:12]:
                parts.append(s.strip())
        else:
            parts.append("\nExtracto:")
            parts.append(_clip_quote(primary.chunk.text, 800))

        if mode == ResponseMode.SEMI and len(ordered) > 1:
            parts.append("\nEvidencia adicional:")
            for it in ordered[1:3]:
                parts.append(
                    f"- {it.chunk.title} p.{it.chunk.page_start}: {_clip_quote(it.chunk.text, 220)}"
                )

        parts.append(
            "\nNota: esta respuesta se limita a la documentación recuperada. "
            "Si el procedimiento vigente a bordo difiere, prevalece el documento oficial sellado."
        )

        status = AnswerStatus.CONFLICT if conflicts else AnswerStatus.OK
        return QueryResponse(
            status=status,
            answer="\n".join(parts),
            citations=cites,
            conflicts=conflicts,
            confidence=float(primary.score),
            mode_used=mode if mode != ResponseMode.AUTO else ResponseMode.EXTRACTIVE,
        )


class LocalLLMGenerator:
    """Generación con llama-cpp si hay GGUF local. Siempre ancla a citas."""

    def __init__(self, model_path: str, n_ctx: int = 4096, n_gpu_layers: int = 0,
                 temperature: float = 0.1, max_tokens: int = 512) -> None:
        from llama_cpp import Llama

        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(
        self,
        query: str,
        items: list[ScoredChunk],
        mode: ResponseMode,
        max_citations: int = 5,
    ) -> QueryResponse:
        # Base extractiva como ancla; el LLM solo reformatea con citas obligatorias
        base = ExtractiveGenerator().generate(query, items, ResponseMode.SEMI, max_citations)
        context = "\n\n".join(
            f"[{i+1}] {c.title} p.{c.page_start} §{c.section or '-'}\n{c.quote}"
            for i, c in enumerate(base.citations)
        )
        prompt = (
            "Eres un asistente técnico naval OFFLINE. "
            "SOLO puedes usar la evidencia proporcionada. "
            "Si no está en la evidencia, di NO ENCONTRADO. "
            "Incluye referencias [n] a las citas. No inventes pasos.\n\n"
            f"EVIDENCIA:\n{context}\n\nPREGUNTA: {query}\n\nRESPUESTA:"
        )
        out = self.llm(
            prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stop=["PREGUNTA:", "EVIDENCIA:"],
        )
        text = out["choices"][0]["text"].strip()
        return QueryResponse(
            status=base.status,
            answer=text or base.answer,
            citations=base.citations,
            conflicts=base.conflicts,
            confidence=base.confidence,
            mode_used=ResponseMode.GENERATIVE,
        )


def build_generator(cfg: AppConfig | None = None):
    cfg = cfg or load_config()
    if cfg.models.llm.enabled:
        path = cfg.resolve(cfg.models.llm.name_or_path)
        if path.exists():
            try:
                return LocalLLMGenerator(
                    str(path),
                    n_ctx=cfg.models.llm.n_ctx,
                    n_gpu_layers=cfg.models.llm.n_gpu_layers,
                    temperature=cfg.models.llm.temperature,
                    max_tokens=cfg.models.llm.max_tokens,
                )
            except Exception as exc:
                logger.warning("LLM local no cargó (%s). Usando extractivo.", exc)
    return ExtractiveGenerator()
