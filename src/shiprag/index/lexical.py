"""Almacén lexical BM25 persistente por zona."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from shiprag.core.schemas import Chunk, Criticality, DocType, Zone

logger = logging.getLogger("shiprag.lexical")

TOKEN_RE = re.compile(r"[a-záéíóúñü0-9]+", re.I)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class LexicalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._chunks: dict[str, Chunk] = {}
        self._corpus_tokens: list[list[str]] = []
        self._ids: list[str] = []
        self._bm25: BM25Okapi | None = None
        self._load()

    def _meta_path(self) -> Path:
        return self.path / "lexical.jsonl"

    def _load(self) -> None:
        fp = self._meta_path()
        if not fp.exists():
            return
        self._chunks.clear()
        self._corpus_tokens = []
        self._ids = []
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                ch = Chunk.model_validate(obj)
                self._chunks[ch.chunk_id] = ch
                self._ids.append(ch.chunk_id)
                self._corpus_tokens.append(tokenize(ch.text))
        if self._corpus_tokens:
            self._bm25 = BM25Okapi(self._corpus_tokens)
        logger.debug("LexicalStore %s: %d docs", self.path, len(self._ids))

    def _rebuild(self) -> None:
        self._ids = list(self._chunks.keys())
        self._corpus_tokens = [tokenize(self._chunks[i].text) for i in self._ids]
        self._bm25 = BM25Okapi(self._corpus_tokens) if self._corpus_tokens else None
        with self._meta_path().open("w", encoding="utf-8") as f:
            for cid in self._ids:
                f.write(self._chunks[cid].model_dump_json() + "\n")

    def upsert(self, chunks: list[Chunk]) -> int:
        for ch in chunks:
            self._chunks[ch.chunk_id] = ch
        self._rebuild()
        return len(chunks)

    def delete_doc(self, doc_id: str) -> int:
        before = len(self._chunks)
        self._chunks = {k: v for k, v in self._chunks.items() if v.doc_id != doc_id}
        removed = before - len(self._chunks)
        if removed:
            self._rebuild()
        return removed

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
        if not self._bm25 or not self._ids:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        crit_order = {
            Criticality.LOW: 0,
            Criticality.MEDIUM: 1,
            Criticality.HIGH: 2,
            Criticality.CRITICAL: 3,
        }
        out: list[tuple[Chunk, float, int]] = []
        rank = 0
        for idx, score in ranked:
            if score <= 0:
                break
            ch = self._chunks[self._ids[idx]]
            if zones and ch.zone not in zones:
                continue
            if doc_types and ch.doc_type not in doc_types:
                continue
            if languages and ch.language not in languages:
                continue
            if criticality_min and crit_order[ch.criticality] < crit_order[criticality_min]:
                continue
            rank += 1
            out.append((ch, float(score), rank))
            if len(out) >= top_k:
                break
        return out

    def __len__(self) -> int:
        return len(self._chunks)
