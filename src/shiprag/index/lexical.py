"""Almacén lexical BM25 persistente por zona."""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from shiprag.core.schemas import Chunk, Criticality, DocType, Zone

logger = logging.getLogger("shiprag.lexical")

TOKEN_RE = re.compile(r"[a-záéíóúñü0-9]+", re.I)

CRIT_ORDER = {
    Criticality.LOW: 0,
    Criticality.MEDIUM: 1,
    Criticality.HIGH: 2,
    Criticality.CRITICAL: 3,
}


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class _Snapshot:
    """Estado inmutable que leen las búsquedas; cada commit lo sustituye entero."""

    chunks: tuple[Chunk, ...] = ()
    bm25: BM25Okapi | None = None


class LexicalStore:
    """BM25 en memoria + JSONL en disco.

    - Escrituras: actualizan el diccionario de chunks; el commit (rebuild BM25 +
      persistencia) es O(N). Con `deferred=True` (lo activa `HybridIndex.batch()`)
      el commit se aplaza al final del lote: ingerir un directorio cuesta un único
      rebuild en vez de uno por fichero.
    - Lecturas: trabajan sobre un snapshot inmutable, así una búsqueda concurrente
      nunca ve un índice a medio reconstruir.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._chunks: dict[str, Chunk] = {}
        self._by_doc: dict[str, set[str]] = {}
        self._snapshot = _Snapshot()
        self._lock = threading.RLock()
        self._dirty = False
        self.deferred = False
        self.commits = 0
        self._load()

    def _meta_path(self) -> Path:
        return self.path / "lexical.jsonl"

    def _load(self) -> None:
        fp = self._meta_path()
        if not fp.exists():
            return
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                self._put(Chunk.model_validate_json(line))
        self._snapshot = self._build_snapshot()
        logger.debug("LexicalStore %s: %d docs", self.path, len(self._chunks))

    def _put(self, ch: Chunk) -> None:
        prev = self._chunks.get(ch.chunk_id)
        if prev is not None and prev.doc_id != ch.doc_id:
            self._by_doc.get(prev.doc_id, set()).discard(ch.chunk_id)
        self._chunks[ch.chunk_id] = ch
        self._by_doc.setdefault(ch.doc_id, set()).add(ch.chunk_id)

    def _build_snapshot(self) -> _Snapshot:
        chunks = tuple(self._chunks.values())
        if not chunks:
            return _Snapshot()
        # Tokens transitorios: no se guardan (rank_bm25 ya mantiene sus frecuencias)
        return _Snapshot(chunks=chunks, bm25=BM25Okapi([tokenize(c.text) for c in chunks]))

    def _persist(self) -> None:
        fp = self._meta_path()
        tmp = fp.with_name(fp.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for ch in self._chunks.values():
                f.write(ch.model_dump_json() + "\n")
        # Reemplazo atómico: un corte a mitad de escritura no deja el índice corrupto
        os.replace(tmp, fp)

    def commit(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._persist()
            self._snapshot = self._build_snapshot()
            self._dirty = False
            self.commits += 1

    def _after_write(self) -> None:
        self._dirty = True
        if not self.deferred:
            self.commit()

    def upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        with self._lock:
            for ch in chunks:
                self._put(ch)
            self._after_write()
        return len(chunks)

    def delete_doc(self, doc_id: str) -> int:
        with self._lock:
            ids = self._by_doc.pop(doc_id, set())
            for cid in ids:
                self._chunks.pop(cid, None)
            if ids:
                self._after_write()
        return len(ids)

    def doc_chunk_count(self, doc_id: str) -> int:
        return len(self._by_doc.get(doc_id, ()))

    def search(
        self,
        query: str,
        top_k: int = 40,
        *,
        zones: list[Zone] | None = None,
        doc_types: list[DocType] | None = None,
        languages: list[str] | None = None,
        criticality_min: Criticality | None = None,
    ) -> list[tuple[Chunk, float, int]]:
        snap = self._snapshot
        if snap.bm25 is None or not snap.chunks:
            return []
        scores = snap.bm25.get_scores(tokenize(query))
        order = np.argsort(-scores, kind="stable")
        out: list[tuple[Chunk, float, int]] = []
        rank = 0
        for idx in order:
            score = float(scores[idx])
            if score <= 0:
                break
            ch = snap.chunks[idx]
            if zones and ch.zone not in zones:
                continue
            if doc_types and ch.doc_type not in doc_types:
                continue
            if languages and ch.language not in languages:
                continue
            if criticality_min and CRIT_ORDER[ch.criticality] < CRIT_ORDER[criticality_min]:
                continue
            rank += 1
            out.append((ch, score, rank))
            if len(out) >= top_k:
                break
        return out

    def __len__(self) -> int:
        return len(self._chunks)
