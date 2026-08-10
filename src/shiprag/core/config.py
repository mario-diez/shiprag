"""Configuración tipada con perfiles de hardware (lite / home / server)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = ROOT / "config" / "default.yaml"
PROFILES_DIR = ROOT / "config" / "profiles"
KNOWN_PROFILES = ("lite", "home", "workstation", "server")


class ProfileInfo(BaseModel):
    id: str = "lite"
    label: str = "Lite"
    description: str = ""


class EmbeddingConfig(BaseModel):
    # backend: hash | auto | sentence_transformers
    backend: str = "auto"
    name_or_path: str = "models/embeddings/multilingual-e5-small"
    fallback: str = "hash"
    device: str = "cpu"
    normalize: bool = True
    dim: int = 384


class RerankerConfig(BaseModel):
    # backend: lexical | auto | cross_encoder
    backend: str = "auto"
    name_or_path: str = "models/reranker/bge-reranker-base"
    fallback: str = "lexical"
    device: str = "cpu"


class LLMConfig(BaseModel):
    enabled: bool = False
    name_or_path: str = "models/llm/model.gguf"
    n_ctx: int = 4096
    n_gpu_layers: int = 0
    temperature: float = 0.1
    max_tokens: int = 512


class ModelsConfig(BaseModel):
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


class RetrievalConfig(BaseModel):
    lexical_top_k: int = 40
    dense_top_k: int = 40
    rrf_k: int = 60
    rerank_top_k: int = 8
    min_rrf_score: float = 0.001
    min_evidence_score: float = 0.18
    emergency_min_evidence_score: float = 0.28


class ChunkingConfig(BaseModel):
    max_chars: int = 1200
    overlap_chars: int = 150
    min_chars: int = 80
    prefer_structural: bool = True


class OCRConfig(BaseModel):
    enabled: bool = True
    lang: str = "spa+eng"
    min_text_chars_per_page: int = 40


class GenerationConfig(BaseModel):
    default_mode: str = "auto"
    emergency_mode: str = "extractive"
    max_citations: int = 5
    grounding_threshold: float = 0.35
    emergency_grounding_threshold: float = 0.55
    clarify_on_low_confidence: bool = True


class ZoneConfig(BaseModel):
    id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    default_response_mode: str | None = None
    criticality: str | None = None


class RouterConfig(BaseModel):
    confidence_threshold: float = 0.45
    allow_multi_expert: bool = True
    max_experts: int = 3
    fallback_zone: str = "general"


class PathsConfig(BaseModel):
    data_dir: str = "data"
    raw_dir: str = "data/raw"
    index_dir: str = "data/indexes/lite"
    log_dir: str = "data/logs"
    models_dir: str = "models"
    sample_dir: str = "data/sample"


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    retrieval_trace: bool = True


class AppConfig(BaseModel):
    profile: ProfileInfo = Field(default_factory=ProfileInfo)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    zones: list[ZoneConfig] = Field(default_factory=list)
    api: APIConfig = Field(default_factory=APIConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return (ROOT / p).resolve()

    @property
    def index_path(self) -> Path:
        return self.resolve(self.paths.index_dir)

    @property
    def raw_path(self) -> Path:
        return self.resolve(self.paths.raw_dir)

    @property
    def log_path(self) -> Path:
        return self.resolve(self.paths.log_dir)

    @property
    def models_path(self) -> Path:
        return self.resolve(self.paths.models_dir)

    def zone_map(self) -> dict[str, ZoneConfig]:
        return {z.id: z for z in self.zones}

    def runtime_summary(self) -> dict[str, Any]:
        return {
            "profile": self.profile.id,
            "profile_label": self.profile.label,
            "embedding_backend": self.models.embedding.backend,
            "reranker_backend": self.models.reranker.backend,
            "llm_enabled": self.models.llm.enabled,
            "index_dir": str(self.index_path),
            "device_embedding": self.models.embedding.device,
            "device_reranker": self.models.reranker.device,
        }


class Settings(BaseSettings):
    """Overrides por entorno."""

    shiprag_config: str | None = None
    shiprag_profile: str | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config inválida en {path}")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge recursivo: overlay gana. Listas se reemplazan enteras."""
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_profile_path(profile: str) -> Path:
    name = profile.strip().lower()
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        known = ", ".join(KNOWN_PROFILES)
        raise FileNotFoundError(
            f"Perfil desconocido '{profile}'. Disponibles: {known}. "
            f"También puedes pasar --config ruta/a/custom.yaml"
        )
    return path


def list_profiles() -> list[dict[str, str]]:
    profiles = []
    for name in KNOWN_PROFILES:
        path = PROFILES_DIR / f"{name}.yaml"
        if not path.exists():
            continue
        raw = _load_yaml(path)
        info = raw.get("profile") or {}
        profiles.append(
            {
                "id": str(info.get("id", name)),
                "label": str(info.get("label", name)),
                "description": str(info.get("description", "")).strip(),
                "path": str(path),
            }
        )
    return profiles


@lru_cache(maxsize=16)
def load_config(
    config_path: str | None = None,
    profile: str | None = None,
) -> AppConfig:
    """Carga default.yaml y aplica un perfil encima.

    Prioridad de perfil:
      1. argumento `profile`
      2. env SHIPRAG_PROFILE
      3. valor `profile.id` del YAML base (lite)
    """
    settings = Settings()
    base_path = Path(config_path or settings.shiprag_config or DEFAULT_CONFIG_PATH)
    if not base_path.is_absolute():
        base_path = (ROOT / base_path).resolve()
    raw = _load_yaml(base_path)

    chosen = (profile or settings.shiprag_profile or (raw.get("profile") or {}).get("id") or "lite")
    chosen = str(chosen).strip().lower()
    # Si el usuario pasa un config custom completo que ya es un perfil, no re-mergear
    # salvo que pida explícitamente otro profile distinto del embebido.
    embedded = str((raw.get("profile") or {}).get("id") or "").strip().lower()
    if chosen and chosen != embedded:
        overlay = _load_yaml(resolve_profile_path(chosen))
        raw = _deep_merge(raw, overlay)
    elif chosen and embedded == chosen and config_path is None:
        # default.yaml ya es lite; aún así mergear profile file por si diverge
        try:
            overlay = _load_yaml(resolve_profile_path(chosen))
            raw = _deep_merge(raw, overlay)
        except FileNotFoundError:
            pass

    cfg = AppConfig.model_validate(raw)
    # Asegurar id de perfil coherente
    if not cfg.profile.id:
        cfg.profile.id = chosen
    return cfg


def clear_config_cache() -> None:
    load_config.cache_clear()


def ensure_runtime_dirs(cfg: AppConfig | None = None) -> None:
    cfg = cfg or load_config()
    for p in (cfg.index_path, cfg.raw_path, cfg.log_path, cfg.models_path):
        p.mkdir(parents=True, exist_ok=True)
