"""Embeddings locales con fallback determinista (sin red en runtime)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np

from shiprag.core.config import AppConfig, load_config

logger = logging.getLogger("shiprag.embeddings")


class EmbeddingBackend:
    def embed_documents(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        raise NotImplementedError

    @property
    def dim(self) -> int:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError


class HashEmbedding(EmbeddingBackend):
    """Embedding bag-of-hashes: útil para tests y arranque sin pesos.

    No es SOTA, pero es 100% offline y estable. En puerto, sustituir por
    sentence-transformers multilingual.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"hash-{self._dim}"

    def _one(self, text: str) -> np.ndarray:
        vec = np.zeros(self._dim, dtype=np.float32)
        tokens = text.lower().split()
        if not tokens:
            return vec
        for tok in tokens:
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "little") % self._dim
            sign = 1.0 if h[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._one(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._one(text)


class SentenceTransformerEmbedding(EmbeddingBackend):
    def __init__(self, name_or_path: str, device: str = "cpu", normalize: bool = True) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(name_or_path, device=device)
        self._normalize = normalize
        self._name = name_or_path
        # Infer dim
        self._dim = int(self._model.get_sentence_embedding_dimension())

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._name

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        # E5 models expect "passage: " prefix; apply softly if name contains e5
        prefix = "passage: " if "e5" in self._name.lower() else ""
        embs = self._model.encode(
            [prefix + t for t in texts],
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        return np.asarray(embs, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        prefix = "query: " if "e5" in self._name.lower() else ""
        emb = self._model.encode(
            prefix + text,
            normalize_embeddings=self._normalize,
            show_progress_bar=False,
        )
        return np.asarray(emb, dtype=np.float32)


def _resolve_model_candidate(cfg: AppConfig, name_or_path: str | None) -> str | None:
    if not name_or_path:
        return None
    path = cfg.resolve(name_or_path)
    if path.exists():
        return str(path)
    return name_or_path if Path(name_or_path).exists() else None


def build_embedder(cfg: AppConfig | None = None) -> EmbeddingBackend:
    cfg = cfg or load_config()
    backend = (cfg.models.embedding.backend or "auto").lower().strip()
    dim = int(cfg.models.embedding.dim or 384)

    if backend == "hash" or cfg.models.embedding.fallback == "force_hash":
        logger.info("Perfil fuerza HashEmbedding (backend=%s)", backend)
        return HashEmbedding(dim=dim)

    candidates: list[str] = []
    primary = _resolve_model_candidate(cfg, cfg.models.embedding.name_or_path)
    if primary:
        candidates.append(primary)
    fb = _resolve_model_candidate(cfg, cfg.models.embedding.fallback_name_or_path)
    if fb and fb not in candidates:
        candidates.append(fb)

    if backend in {"auto", "sentence_transformers", "st"}:
        for candidate in candidates:
            try:
                logger.info("Cargando embeddings desde %s", candidate)
                return SentenceTransformerEmbedding(
                    candidate,
                    device=cfg.models.embedding.device,
                    normalize=cfg.models.embedding.normalize,
                )
            except Exception as exc:
                logger.warning("No se pudo cargar ST embeddings %s (%s).", candidate, exc)

    if backend == "sentence_transformers":
        logger.warning(
            "backend=sentence_transformers pero no hay modelo en %s → HashEmbedding",
            cfg.models.embedding.name_or_path,
        )

    logger.info(
        "Usando HashEmbedding (profile=%s backend=%s). "
        "Para mejor calidad en casa: shiprag --profile home … "
        "tras python scripts/download_models.py --profile home",
        cfg.profile.id,
        backend,
    )
    return HashEmbedding(dim=dim)
