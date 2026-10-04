"""Registro de documentos indexados: identidad, versión y huella de ingesta.

Persistencia: un JSON por documento en `<index>/documents/<doc_id>.json`
(mismo formato que ya escribía la ingesta; los campos nuevos son opcionales,
así que los índices antiguos se leen sin migración).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from shiprag.core.schemas import DocumentMeta

logger = logging.getLogger("shiprag.registry")


class DocumentRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._docs: dict[str, DocumentMeta] = {}
        self._by_source: dict[str, str] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self.root.exists():
            return
        for p in sorted(self.root.glob("*.json")):
            try:
                meta = DocumentMeta.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Registro ilegible %s (%s); se ignora", p, exc)
                continue
            self._docs[meta.doc_id] = meta
            if meta.source_uri:
                self._by_source[meta.source_uri] = meta.doc_id

    def _path(self, doc_id: str) -> Path:
        return self.root / f"{doc_id}.json"

    def get(self, doc_id: str) -> DocumentMeta | None:
        with self._lock:
            return self._docs.get(doc_id)

    def find_by_source(self, source_uri: str) -> str | None:
        with self._lock:
            return self._by_source.get(source_uri)

    def put(self, meta: DocumentMeta) -> None:
        with self._lock:
            prev = self._docs.get(meta.doc_id)
            if prev is not None and prev.source_uri and prev.source_uri != meta.source_uri:
                self._by_source.pop(prev.source_uri, None)
            self._docs[meta.doc_id] = meta
            if meta.source_uri:
                self._by_source[meta.source_uri] = meta.doc_id
            self.root.mkdir(parents=True, exist_ok=True)
            fp = self._path(meta.doc_id)
            tmp = fp.with_name(fp.name + ".tmp")
            tmp.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, fp)

    def remove(self, doc_id: str) -> DocumentMeta | None:
        with self._lock:
            meta = self._docs.pop(doc_id, None)
            if meta is not None and meta.source_uri:
                self._by_source.pop(meta.source_uri, None)
            self._path(doc_id).unlink(missing_ok=True)
            return meta

    def all(self) -> list[DocumentMeta]:
        with self._lock:
            return sorted(self._docs.values(), key=lambda m: m.doc_id)

    def __contains__(self, doc_id: str) -> bool:
        return self.get(doc_id) is not None

    def __len__(self) -> int:
        return len(self._docs)
