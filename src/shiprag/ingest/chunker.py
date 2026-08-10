"""Chunking inteligente por estructura documental.

No cortamos a ciegas por N tokens: agrupamos por sección / procedimiento
numerado / aviso, y solo partimos si supera max_chars respetando overlap.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from shiprag.core.config import AppConfig, ChunkingConfig, load_config
from shiprag.core.schemas import (
    Chunk,
    Criticality,
    DocType,
    DocumentMeta,
    ExtractedBlock,
    Zone,
)

NUMBERED_PROC_RE = re.compile(r"(?m)^\s*\d+[\.\)]\s+\S+")
WARNING_RE = re.compile(
    r"\b(PELIGRO|WARNING|DANGER|PRECAUCIÓN|PRECAUCION|CAUTION|AVISO)\b",
    re.I,
)


def _chunk_id(doc_id: str, page: int, text: str) -> str:
    h = hashlib.sha1(f"{doc_id}|{page}|{text[:200]}".encode("utf-8")).hexdigest()[:16]
    return f"{doc_id}_{page}_{h}"


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        # Preferir corte en salto de línea / punto
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind(". "), window.rfind("; "))
            if cut > max_chars * 0.4:
                end = start + cut + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [p for p in parts if p]


class StructuralChunker:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self.cc: ChunkingConfig = self.cfg.chunking

    def chunk(self, meta: DocumentMeta, blocks: Iterable[ExtractedBlock]) -> list[Chunk]:
        blocks = list(blocks)
        if not blocks:
            return []

        # Agrupar por sección manteniendo headings como ancla
        groups: list[list[ExtractedBlock]] = []
        current: list[ExtractedBlock] = []
        for b in blocks:
            if b.block_type == "heading" and current:
                groups.append(current)
                current = [b]
            else:
                current.append(b)
        if current:
            groups.append(current)

        chunks: list[Chunk] = []
        for group in groups:
            section = next((b.section for b in group if b.section), None)
            heading_path = next((b.heading_path for b in group if b.heading_path), [])
            # Concatenar bloques no-heading; incluir heading al inicio como contexto
            texts: list[str] = []
            pages: list[int] = []
            btypes: list[str] = []
            for b in group:
                if b.block_type == "heading":
                    heading = b.text.lstrip("# ").strip()
                    texts.append(f"## {heading}")
                else:
                    texts.append(b.text)
                pages.append(b.page)
                btypes.append(b.block_type)
            full = "\n\n".join(t for t in texts if t.strip()).strip()
            if len(full) < self.cc.min_chars:
                # Fusionar grupos demasiado pequeños se hace en post; por ahora guardar si hay algo
                if not full:
                    continue

            pieces = (
                _split_long(full, self.cc.max_chars, self.cc.overlap_chars)
                if self.cc.prefer_structural
                else _split_long(full, self.cc.max_chars, self.cc.overlap_chars)
            )
            for piece in pieces:
                if len(piece) < self.cc.min_chars and len(pieces) > 1:
                    continue
                page_start = min(pages) if pages else 1
                page_end = max(pages) if pages else 1
                is_proc = bool(NUMBERED_PROC_RE.search(piece))
                has_warn = bool(WARNING_RE.search(piece)) or "warning" in btypes
                chunks.append(
                    Chunk(
                        chunk_id=_chunk_id(meta.doc_id, page_start, piece),
                        doc_id=meta.doc_id,
                        text=piece,
                        page_start=page_start,
                        page_end=page_end,
                        section=section,
                        heading_path=list(heading_path),
                        zone=meta.zone if isinstance(meta.zone, Zone) else Zone(meta.zone),
                        doc_type=meta.doc_type
                        if isinstance(meta.doc_type, DocType)
                        else DocType(meta.doc_type),
                        language=meta.language,
                        criticality=meta.criticality
                        if isinstance(meta.criticality, Criticality)
                        else Criticality(meta.criticality),
                        version=meta.version,
                        source_path=meta.source_path,
                        title=meta.title,
                        block_types=sorted(set(btypes)),
                        is_numbered_procedure=is_proc,
                        has_safety_warning=has_warn,
                    )
                )
        return self._merge_tiny(chunks)

    def _merge_tiny(self, chunks: list[Chunk]) -> list[Chunk]:
        if not chunks:
            return chunks
        merged: list[Chunk] = []
        buf: Chunk | None = None
        for ch in chunks:
            if buf is None:
                buf = ch
                continue
            if len(buf.text) < self.cc.min_chars and buf.section == ch.section:
                buf = buf.model_copy(
                    update={
                        "text": buf.text + "\n\n" + ch.text,
                        "page_end": max(buf.page_end, ch.page_end),
                        "block_types": sorted(set(buf.block_types + ch.block_types)),
                        "is_numbered_procedure": buf.is_numbered_procedure
                        or ch.is_numbered_procedure,
                        "has_safety_warning": buf.has_safety_warning or ch.has_safety_warning,
                    }
                )
            else:
                merged.append(buf)
                buf = ch
        if buf is not None:
            merged.append(buf)
        return merged
