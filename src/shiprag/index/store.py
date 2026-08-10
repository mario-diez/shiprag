"""Índice híbrido: Chroma (denso) + BM25 (lexical) por zona."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config
from shiprag.core.schemas import Chunk, Criticality, DocType, Zone
from shiprag.index.embeddings import EmbeddingBackend, build_embedder
from shiprag.index.lexical import LexicalStore

logger = logging.getLogger("shiprag.index")


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

        import numpy as np

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

        with self._fallback_path().open("w", encoding="utf-8") as f:
            for doc in self._fallback_docs.values():
                payload = {
                    "id": doc["id"],
                    "document": doc["document"],
                    "metadata": doc["metadata"],
                    "embedding": doc["embedding"].tolist(),
                }
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = [c.metadata_dict() for c in chunks]
        embs = self.embedder.embed_documents(docs)
        if self._mode == "chroma" and self._collection is not None:
            # Chroma exige metadatos con tipos simples
            self._collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas,
                embeddings=embs.tolist(),
            )
        else:
            for i, ch in enumerate(chunks):
                self._fallback_docs[ch.chunk_id] = {
                    "id": ch.chunk_id,
                    "document": ch.text,
                    "metadata": metas[i],
                    "embedding": embs[i],
                }
            self._save_fallback()
        return len(chunks)

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
        import numpy as np

        scored = []
        for doc in self._fallback_docs.values():
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
    """Fachada multi-zona: un lexical + un dense por zona, más índice global."""

    def __init__(self, cfg: AppConfig | None = None, embedder: EmbeddingBackend | None = None) -> None:
        self.cfg = cfg or load_config()
        ensure_runtime_dirs(self.cfg)
        self.embedder = embedder or build_embedder(self.cfg)
        self.root = self.cfg.index_path
        self._lexical: dict[str, LexicalStore] = {}
        self._dense: dict[str, DenseStore] = {}

    def _zone_key(self, zone: Zone | str) -> str:
        return zone.value if isinstance(zone, Zone) else str(zone)

    def lexical(self, zone: Zone | str) -> LexicalStore:
        key = self._zone_key(zone)
        if key not in self._lexical:
            self._lexical[key] = LexicalStore(self.root / key / "bm25")
        return self._lexical[key]

    def dense(self, zone: Zone | str) -> DenseStore:
        key = self._zone_key(zone)
        if key not in self._dense:
            self._dense[key] = DenseStore(
                self.root / key / "chroma",
                self.embedder,
                collection=f"shiprag_{key}",
            )
        return self._dense[key]

    def upsert_chunks(self, chunks: list[Chunk], zone: Zone | None = None) -> int:
        if not chunks:
            return 0
        # Agrupar por zona real del chunk
        by_zone: dict[str, list[Chunk]] = {}
        for ch in chunks:
            z = self._zone_key(zone or ch.zone)
            by_zone.setdefault(z, []).append(ch)
        total = 0
        for z, group in by_zone.items():
            self.lexical(z).upsert(group)
            self.dense(z).upsert(group)
            # Espejo en general para consultas globales
            if z != Zone.GENERAL.value:
                self.lexical(Zone.GENERAL).upsert(group)
                self.dense(Zone.GENERAL).upsert(group)
            total += len(group)
        return total

    def zones_with_data(self) -> list[str]:
        found = []
        if not self.root.exists():
            return found
        for p in self.root.iterdir():
            if p.is_dir() and (p / "bm25").exists():
                found.append(p.name)
        return sorted(found)
