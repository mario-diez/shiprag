"""Índice híbrido: Chroma (denso) + BM25 (lexical) por zona."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config
from shiprag.core.schemas import Chunk, Zone
from shiprag.index.embeddings import EmbeddingBackend, build_embedder
from shiprag.index.lexical import LexicalStore

logger = logging.getLogger("shiprag.index")

# Chroma rechaza lotes por encima de su máximo (~5k); un PDF grande lo supera
CHROMA_BATCH = 1000


class DenseStore:
    """Chroma persistente por zona. Si chroma no carga, usamos fallback JSON+numpy."""

    def __init__(self, path: Path, embedder: EmbeddingBackend, collection: str) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.collection_name = collection
        self._mode = "chroma"
        self._collection = None
        self._fallback_docs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self.deferred = False
        self._init()

    def _init(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.PersistentClient(
                path=str(self.path),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._mode = "chroma"
            logger.debug("Chroma listo en %s (%s)", self.path, self.collection_name)
        except Exception as exc:
            logger.warning("Chroma no disponible (%s). Usando fallback denso local.", exc)
            self._mode = "fallback"
            self._load_fallback()

    def _fallback_path(self) -> Path:
        return self.path / f"{self.collection_name}_dense.jsonl"

    def _load_fallback(self) -> None:
        import json

        self._fallback_docs = {}
        fp = self._fallback_path()
        if not fp.exists():
            return
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                obj["embedding"] = np.asarray(obj["embedding"], dtype="float32")
                self._fallback_docs[obj["id"]] = obj

    def _save_fallback(self) -> None:
        import json

        fp = self._fallback_path()
        tmp = fp.with_name(fp.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for doc in self._fallback_docs.values():
                payload = {
                    "id": doc["id"],
                    "document": doc["document"],
                    "metadata": doc["metadata"],
                    "embedding": doc["embedding"].tolist(),
                }
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        os.replace(tmp, fp)

    def commit(self) -> None:
        """Persiste el fallback JSONL (Chroma persiste por sí mismo)."""
        with self._lock:
            if self._dirty and self._mode != "chroma":
                self._save_fallback()
            self._dirty = False

    def _after_write(self) -> None:
        self._dirty = True
        if not self.deferred:
            self.commit()

    def upsert(self, chunks: list[Chunk], embeddings: np.ndarray | None = None) -> int:
        """Inserta chunks; `embeddings` precalculados evitan volver a pasar por el modelo."""
        if not chunks:
            return 0
        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = [c.metadata_dict() for c in chunks]
        embs = embeddings if embeddings is not None else self.embedder.embed_documents(docs)
        with self._lock:
            if self._mode == "chroma" and self._collection is not None:
                # Chroma exige metadatos con tipos simples
                for start in range(0, len(ids), CHROMA_BATCH):
                    end = start + CHROMA_BATCH
                    self._collection.upsert(
                        ids=ids[start:end],
                        documents=docs[start:end],
                        metadatas=metas[start:end],
                        embeddings=np.asarray(embs[start:end]).tolist(),
                    )
            else:
                for i, ch in enumerate(chunks):
                    self._fallback_docs[ch.chunk_id] = {
                        "id": ch.chunk_id,
                        "document": ch.text,
                        "metadata": metas[i],
                        "embedding": np.asarray(embs[i], dtype="float32"),
                    }
                self._after_write()
        return len(chunks)

    def delete_doc(self, doc_id: str) -> int:
        with self._lock:
            if self._mode == "chroma" and self._collection is not None:
                ids = self._collection.get(where={"doc_id": doc_id}, include=[]).get("ids") or []
                for start in range(0, len(ids), CHROMA_BATCH):
                    self._collection.delete(ids=ids[start : start + CHROMA_BATCH])
                return len(ids)
            ids = [k for k, v in self._fallback_docs.items() if v["metadata"].get("doc_id") == doc_id]
            for k in ids:
                del self._fallback_docs[k]
            if ids:
                self._after_write()
            return len(ids)

    def search(
        self,
        query: str,
        top_k: int = 40,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, str, dict[str, Any], float, int]]:
        """Devuelve (id, document, metadata, distance_or_score, rank)."""
        q = self.embedder.embed_query(query)
        if self._mode == "chroma" and self._collection is not None:
            kwargs: dict[str, Any] = {
                "query_embeddings": [q.tolist()],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where
            try:
                res = self._collection.query(**kwargs)
            except Exception as exc:
                # where malformado o mismatch de dimensión → degradar
                msg = str(exc).lower()
                if where and "where" in msg:
                    kwargs.pop("where", None)
                    try:
                        res = self._collection.query(**kwargs)
                    except Exception:
                        logger.warning("Chroma query falló (%s); sin resultados densos", exc)
                        return []
                else:
                    logger.warning("Chroma query falló (%s); sin resultados densos", exc)
                    return []
            out = []
            ids = (res.get("ids") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            for rank, (i, d, m, dist) in enumerate(zip(ids, docs, metas, dists), start=1):
                # cosine distance → score = 1 - dist (aprox)
                score = 1.0 - float(dist)
                out.append((i, d, m or {}, score, rank))
            return out

        # fallback brute force
        scored = []
        for doc in list(self._fallback_docs.values()):
            md = doc["metadata"]
            if where:
                ok = True
                for k, v in where.items():
                    if isinstance(v, dict) and "$in" in v:
                        if md.get(k) not in v["$in"]:
                            ok = False
                            break
                    elif md.get(k) != v:
                        ok = False
                        break
                if not ok:
                    continue
            sim = float(np.dot(q, doc["embedding"]))
            scored.append((doc["id"], doc["document"], md, sim))
        scored.sort(key=lambda x: x[3], reverse=True)
        return [
            (i, d, m, s, rank)
            for rank, (i, d, m, s) in enumerate(scored[:top_k], start=1)
        ]

    def __len__(self) -> int:
        if self._mode == "chroma" and self._collection is not None:
            return int(self._collection.count())
        return len(self._fallback_docs)


class HybridIndex:
    """Fachada multi-zona: un lexical + un dense por zona, más índice global.

    Escritura: un solo escritor a la vez dentro del proceso (`_write_lock`).
    Lectura: sin bloqueo (los stores lexicales sirven snapshots inmutables).
    """

    def __init__(self, cfg: AppConfig | None = None, embedder: EmbeddingBackend | None = None) -> None:
        self.cfg = cfg or load_config()
        ensure_runtime_dirs(self.cfg)
        self.embedder = embedder or build_embedder(self.cfg)
        self.root = self.cfg.index_path
        self._lexical: dict[str, LexicalStore] = {}
        self._dense: dict[str, DenseStore] = {}
        self._stores_lock = threading.Lock()
        self._write_lock = threading.RLock()
        self._batch_depth = 0

    def _zone_key(self, zone: Zone | str) -> str:
        return zone.value if isinstance(zone, Zone) else str(zone)

    def lexical(self, zone: Zone | str) -> LexicalStore:
        key = self._zone_key(zone)
        with self._stores_lock:
            if key not in self._lexical:
                store = LexicalStore(self.root / key / "bm25")
                store.deferred = self._batch_depth > 0
                self._lexical[key] = store
            return self._lexical[key]

    def dense(self, zone: Zone | str) -> DenseStore:
        key = self._zone_key(zone)
        with self._stores_lock:
            if key not in self._dense:
                store = DenseStore(
                    self.root / key / "chroma",
                    self.embedder,
                    collection=f"shiprag_{key}",
                )
                store.deferred = self._batch_depth > 0
                self._dense[key] = store
            return self._dense[key]

    def _open_stores(self) -> list[LexicalStore | DenseStore]:
        with self._stores_lock:
            return [*self._lexical.values(), *self._dense.values()]

    @contextmanager
    def batch(self) -> Iterator[HybridIndex]:
        """Agrupa escrituras: los BM25 se reconstruyen y persisten una vez al salir."""
        with self._write_lock:
            self._batch_depth += 1
            for store in self._open_stores():
                store.deferred = True
            try:
                yield self
            finally:
                self._batch_depth -= 1
                if self._batch_depth == 0:
                    for store in self._open_stores():
                        store.deferred = False
                        store.commit()

    def _upsert_embedded(
        self,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        zone: Zone | None = None,
    ) -> int:
        by_zone: dict[str, list[int]] = {}
        for i, ch in enumerate(chunks):
            by_zone.setdefault(self._zone_key(zone or ch.zone), []).append(i)
        total = 0
        for z, idxs in by_zone.items():
            group = [chunks[i] for i in idxs]
            group_embs = embeddings[idxs]
            self.lexical(z).upsert(group)
            self.dense(z).upsert(group, embeddings=group_embs)
            # Espejo en general para consultas globales (mismos embeddings, sin recalcular)
            if z != Zone.GENERAL.value:
                self.lexical(Zone.GENERAL).upsert(group)
                self.dense(Zone.GENERAL).upsert(group, embeddings=group_embs)
            total += len(group)
        return total

    def _embed(self, chunks: list[Chunk]) -> np.ndarray:
        return np.asarray(self.embedder.embed_documents([c.text for c in chunks]))

    def upsert_chunks(self, chunks: list[Chunk], zone: Zone | None = None) -> int:
        if not chunks:
            return 0
        embeddings = self._embed(chunks)
        with self._write_lock:
            return self._upsert_embedded(chunks, embeddings, zone)

    def delete_document(self, doc_id: str) -> int:
        """Borra todos los chunks de `doc_id` (zona + espejo general).

        Devuelve el nº de chunks distintos eliminados. El BM25 en memoria dice en qué
        zonas está el documento (O(1)); solo ahí se consulta/borra en Chroma, así un
        documento nuevo no cuesta una consulta por zona.
        """
        with self._write_lock:
            existing = set(self.zones_with_data()) | set(self._lexical)
            removed_general = 0
            removed_other = 0
            for key in sorted(existing):
                n = self.lexical(key).delete_doc(doc_id)
                if not n:
                    continue
                self.dense(key).delete_doc(doc_id)
                if key == Zone.GENERAL.value:
                    removed_general = n
                else:
                    removed_other += n
            # Cada chunk vive en general (espejo o zona propia): es el recuento real
            return removed_general or removed_other

    def replace_document(
        self,
        doc_id: str,
        chunks: list[Chunk],
        *,
        zone: Zone | None = None,
    ) -> tuple[int, int]:
        """Sustituye la versión indexada de un documento. Devuelve (borrados, insertados).

        Los embeddings se calculan antes de borrar: si el modelo falla (OOM, etc.)
        la versión anterior sigue intacta.
        """
        embeddings = self._embed(chunks) if chunks else None
        with self._write_lock:
            removed = self.delete_document(doc_id)
            added = (
                self._upsert_embedded(chunks, embeddings, zone)
                if chunks and embeddings is not None
                else 0
            )
            return removed, added

    def zones_with_data(self) -> list[str]:
        found = []
        if not self.root.exists():
            return found
        for p in self.root.iterdir():
            if p.is_dir() and (p / "bm25").exists():
                found.append(p.name)
        return sorted(found)
