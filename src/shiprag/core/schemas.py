"""Esquemas de dominio: documentos, chunks, consultas y respuestas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Zone(str, Enum):
    PUENTE = "puente"
    MAQUINARIA = "maquinaria"
    CUBIERTA = "cubierta"
    SEGURIDAD = "seguridad"
    EMERGENCIAS = "emergencias"
    ELECTRICIDAD = "electricidad"
    COMUNICACIONES = "comunicaciones"
    POSICIONAMIENTO_DINAMICO = "posicionamiento_dinamico"
    GENERAL = "general"


class DocType(str, Enum):
    MANUAL = "manual"
    PROCEDURE = "procedure"
    CHECKLIST = "checklist"
    EMERGENCY = "emergency"
    DRAWING = "drawing"
    DIAGRAM = "diagram"
    MAP = "map"
    TECHNICAL = "technical"
    OTHER = "other"


class Criticality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResponseMode(str, Enum):
    AUTO = "auto"
    EXTRACTIVE = "extractive"
    SEMI = "semi"
    GENERATIVE = "generative"
    CITATIONS_ONLY = "citations_only"


class AnswerStatus(str, Enum):
    OK = "ok"
    ABSTAIN = "abstain"
    CLARIFY = "clarify"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"


class DocumentMeta(BaseModel):
    doc_id: str
    title: str
    source_path: str
    version: str = "1.0"
    zone: Zone = Zone.GENERAL
    doc_type: DocType = DocType.OTHER
    language: str = "es"
    criticality: Criticality = Criticality.MEDIUM
    date: str | None = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    # Ciclo de vida en el índice (vacíos en índices creados antes de existir estos campos)
    source_uri: str = ""  # identidad estable del original (ruta absoluta / upload)
    content_hash: str = ""  # sha256 del fichero original
    ingest_fingerprint: str = ""  # hash de contenido + metadatos + config de ingesta
    ingested_at: str | None = None
    chunk_count: int = 0


class ExtractedBlock(BaseModel):
    """Bloque estructural extraído de una página."""

    text: str
    page: int
    section: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    block_type: str = "paragraph"  # paragraph|heading|list|table|warning|figure_caption|ocr
    bbox: list[float] | None = None
    is_ocr: bool = False


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    page_start: int
    page_end: int
    section: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    zone: Zone = Zone.GENERAL
    doc_type: DocType = DocType.OTHER
    language: str = "es"
    criticality: Criticality = Criticality.MEDIUM
    version: str = "1.0"
    source_path: str = ""
    title: str = ""
    block_types: list[str] = Field(default_factory=list)
    is_numbered_procedure: bool = False
    has_safety_warning: bool = False
    created_at: str = Field(default_factory=_utc_now_iso)

    def metadata_dict(self) -> dict[str, Any]:
        """Metadatos serializables para el vector store (tipos simples)."""
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section": self.section or "",
            "heading_path": " > ".join(self.heading_path),
            "zone": self.zone.value,
            "doc_type": self.doc_type.value,
            "language": self.language,
            "criticality": self.criticality.value,
            "version": self.version,
            "source_path": self.source_path,
            "title": self.title,
            "is_numbered_procedure": self.is_numbered_procedure,
            "has_safety_warning": self.has_safety_warning,
        }


class RetrievalFilters(BaseModel):
    zones: list[Zone] | None = None
    doc_types: list[DocType] | None = None
    languages: list[str] | None = None
    criticality_min: Criticality | None = None
    emergency_type: str | None = None
    ship_system: str | None = None


class QueryRequest(BaseModel):
    query: str
    zone: Zone | None = None
    filters: RetrievalFilters | None = None
    mode: ResponseMode = ResponseMode.AUTO
    top_k: int | None = None
    emergency: bool = False
    # Pregunta literal del usuario si `query` es una reescritura (seguimiento en chat)
    original_query: str | None = None


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float
    lexical_rank: int | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    selection_reason: str = ""


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    version: str
    page_start: int
    page_end: int
    section: str | None = None
    quote: str
    score: float
    source_path: str = ""


class ConflictInfo(BaseModel):
    topic: str
    sources: list[Citation]
    detail: str


class RetrievalTrace(BaseModel):
    routed_zones: list[str]
    router_confidence: float
    router_reason: str
    mode: ResponseMode
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    candidates_lexical: int = 0
    candidates_dense: int = 0
    after_rrf: int = 0
    after_rerank: int = 0
    selected: list[dict[str, Any]] = Field(default_factory=list)
    evidence_score: float = 0.0
    grounding_score: float = 0.0
    abstain_reason: str | None = None
    original_query: str | None = None
    generation: dict[str, Any] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    status: AnswerStatus
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    conflicts: list[ConflictInfo] = Field(default_factory=list)
    clarification_question: str | None = None
    confidence: float = 0.0
    mode_used: ResponseMode = ResponseMode.EXTRACTIVE
    zones_used: list[str] = Field(default_factory=list)
    trace: RetrievalTrace | None = None
    # Backend, modelo, latencia y motivo de fallback del LLM (vacío en extractivo)
    generation_info: dict[str, Any] = Field(default_factory=dict)
