"""Pipeline de ingesta offline: extract → chunk → index."""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config
from shiprag.core.schemas import (
    Criticality,
    DocType,
    DocumentMeta,
    Zone,
)
from shiprag.ingest.chunker import StructuralChunker
from shiprag.ingest.pdf_extractor import DocumentExtractor
from shiprag.index.store import HybridIndex

logger = logging.getLogger("shiprag.ingest")

ZONE_HINTS = {
    Zone.EMERGENCIAS: [
        "emergencias",
        "sopep",
        "abandono",
        "hombre al agua",
        "man overboard",
        "mayday",
        " derrame",
        "mob",
    ],
    Zone.POSICIONAMIENTO_DINAMICO: [
        "posicionamiento dinámico",
        "posicionamiento dinamico",
        "dynamic positioning",
        " thruster",
        "watch circle",
        "dp class",
        "dp3",
        "dp-3",
        "loss of position",
        "prs ",
        "dgps",
        "hydroacoustic",
        "joystick dp",
    ],
    Zone.PUENTE: ["puente", "bridge", "naveg", "ecdis", "colreg", "guardia"],
    Zone.MAQUINARIA: ["maquin", "motor", "engine", "propul", "generador", "diésel", "diesel"],
    Zone.CUBIERTA: ["cubierta", "deck", "carga", "amarre"],
    Zone.SEGURIDAD: ["seguridad", "ism", "lsa", "incendio", "fire"],
    Zone.ELECTRICIDAD: ["eléctric", "electric", "blackout", "cuadro", "440 v", "440v"],
    Zone.COMUNICACIONES: ["gmdss", "vhf", "distress", "epirb", "dsc"],
}

# Metadatos embebidos en cabecera de documentos de texto
META_ZONE_RE = re.compile(r"(?im)^#\s*zona\s*:\s*(.+)$")
META_TYPE_RE = re.compile(r"(?im)^#\s*(?:tipo|doc(?:umento)?(?:\s+tipo)?)\s*:\s*(.+)$")
META_CRIT_RE = re.compile(r"(?im)^#\s*criticidad\s*:\s*(.+)$")


def _parse_header_zone(text: str) -> Zone | None:
    m = META_ZONE_RE.search(text[:2000])
    if not m:
        # Formato sample: "# Zona: Emergencias / Cubierta / Puente"
        return None
    raw = m.group(1).lower()
    # Tomar la primera zona reconocible
    order = [
        Zone.EMERGENCIAS,
        Zone.POSICIONAMIENTO_DINAMICO,
        Zone.COMUNICACIONES,
        Zone.ELECTRICIDAD,
        Zone.MAQUINARIA,
        Zone.PUENTE,
        Zone.CUBIERTA,
        Zone.SEGURIDAD,
    ]
    for z in order:
        if z.value in raw or z.value[:5] in raw:
            return z
    aliases = {
        "emergencia": Zone.EMERGENCIAS,
        "naveg": Zone.PUENTE,
        "bridge": Zone.PUENTE,
        "máquina": Zone.MAQUINARIA,
        "maquina": Zone.MAQUINARIA,
        "dp": Zone.POSICIONAMIENTO_DINAMICO,
        "dynamic positioning": Zone.POSICIONAMIENTO_DINAMICO,
        "posicionamiento": Zone.POSICIONAMIENTO_DINAMICO,
    }
    for k, z in aliases.items():
        if k in raw:
            return z
    return None


def infer_zone(text: str, forced: Zone | None = None) -> Zone:
    if forced:
        return forced
    header = _parse_header_zone(text)
    if header:
        return header
    low = text.lower()
    # Evitar que "parada de emergencia" meta docs de máquina en emergencias
    low_for_emerg = low.replace("parada de emergencia", " ").replace("emergency stop", " ")
    best, score = Zone.GENERAL, 0.0
    for zone, kws in ZONE_HINTS.items():
        sc = 0.0
        corpus = low_for_emerg if zone == Zone.EMERGENCIAS else low
        for k in kws:
            if k in corpus:
                sc += 1.0 + 0.05 * len(k)
        if sc > score:
            best, score = zone, sc
    return best

TYPE_HINTS = {
    DocType.EMERGENCY: ["sopep", "hombre al agua", "man overboard", "abandono del", "distress"],
    DocType.CHECKLIST: ["checklist", "lista de chequeo", "check-list"],
    DocType.PROCEDURE: ["procedimiento", "procedure"],
    DocType.MANUAL: ["manual"],
    DocType.DRAWING: ["plano", "drawing", "schematic"],
    DocType.DIAGRAM: ["diagrama", "diagram", "esquema"],
    DocType.MAP: ["mapa", "map", "carta"],
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower())
    return s.strip("_")[:80] or "doc"


def _load_sidecar_meta(path: Path) -> dict:
    """Metadatos opcionales junto al documento: foo.pdf.meta.yaml o foo.meta.yaml."""
    import yaml

    candidates = [
        path.with_suffix(path.suffix + ".meta.yaml"),
        path.with_name(path.stem + ".meta.yaml"),
        path.with_suffix(".meta.yaml"),
    ]
    for c in candidates:
        if c.exists():
            data = yaml.safe_load(c.read_text(encoding="utf-8")) or {}
            return data if isinstance(data, dict) else {}
    return {}


def _zone_from_sidecar(sidecar: dict) -> Zone | None:
    if not sidecar.get("zone"):
        return None
    try:
        return Zone(str(sidecar["zone"]).lower())
    except Exception:
        return None


def _type_from_sidecar(sidecar: dict) -> DocType | None:
    if not sidecar.get("doc_type"):
        return None
    try:
        return DocType(str(sidecar["doc_type"]).lower())
    except Exception:
        return None


def infer_doc_type(text: str, forced: DocType | None = None) -> DocType:
    if forced:
        return forced
    low = text.lower()
    for dt, kws in TYPE_HINTS.items():
        if any(k in low for k in kws):
            return dt
    return DocType.TECHNICAL


def infer_criticality(zone: Zone, doc_type: DocType) -> Criticality:
    if zone == Zone.EMERGENCIAS or doc_type == DocType.EMERGENCY:
        return Criticality.CRITICAL
    if zone in {Zone.SEGURIDAD, Zone.ELECTRICIDAD} or doc_type == DocType.PROCEDURE:
        return Criticality.HIGH
    if doc_type == DocType.CHECKLIST:
        return Criticality.HIGH
    return Criticality.MEDIUM


class IngestPipeline:
    def __init__(self, cfg: AppConfig | None = None, index: HybridIndex | None = None) -> None:
        self.cfg = cfg or load_config()
        ensure_runtime_dirs(self.cfg)
        self.extractor = DocumentExtractor(self.cfg)
        self.chunker = StructuralChunker(self.cfg)
        self.index = index or HybridIndex(self.cfg)

    def ingest_file(
        self,
        path: Path,
        *,
        zone: Zone | None = None,
        doc_type: DocType | None = None,
        title: str | None = None,
        version: str = "1.0",
        language: str = "es",
        copy_to_raw: bool = True,
    ) -> dict:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        dest = path
        if copy_to_raw:
            dest_dir = self.cfg.raw_path / (zone.value if zone else "inbox")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            if path.resolve() != dest.resolve():
                shutil.copy2(path, dest)

        blocks = self.extractor.extract(dest)
        sample_text = " ".join(b.text for b in blocks[:8]) + " " + path.name
        sidecar = _load_sidecar_meta(path)
        z = infer_zone(sample_text, zone or _zone_from_sidecar(sidecar))
        dt = infer_doc_type(sample_text, doc_type or _type_from_sidecar(sidecar))
        crit = (
            Criticality(sidecar["criticality"])
            if sidecar.get("criticality")
            else infer_criticality(z, dt)
        )
        doc_id = _slug(str(sidecar.get("doc_id") or path.stem))
        title = title or sidecar.get("title") or path.stem.replace("_", " ")
        version = sidecar.get("version") or version
        language = sidecar.get("language") or language

        meta = DocumentMeta(
            doc_id=doc_id,
            title=title,
            source_path=str(dest),
            version=version,
            zone=z,
            doc_type=dt,
            language=language,
            criticality=crit,
            tags=list(sidecar.get("tags") or []),
            extra={k: v for k, v in sidecar.items() if k not in {
                "doc_id", "title", "version", "zone", "doc_type", "language", "criticality", "tags"
            }},
        )
        chunks = self.chunker.chunk(meta, blocks)
        n = self.index.upsert_chunks(chunks, zone=z)

        meta_path = self.cfg.index_path / "documents" / f"{doc_id}.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")

        logger.info(
            "Ingestado %s → %d chunks (zona=%s tipo=%s criticidad=%s)",
            path.name,
            n,
            z.value,
            dt.value,
            crit.value,
        )
        return {
            "doc_id": doc_id,
            "title": meta.title,
            "zone": z.value,
            "doc_type": dt.value,
            "criticality": crit.value,
            "chunks": n,
            "blocks": len(blocks),
            "source_path": str(dest),
        }

    def ingest_dir(
        self,
        directory: Path,
        *,
        zone: Zone | None = None,
        recursive: bool = True,
    ) -> list[dict]:
        directory = Path(directory)
        patterns = ("*.pdf", "*.txt", "*.md", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff")
        files: list[Path] = []
        for pat in patterns:
            files.extend(directory.rglob(pat) if recursive else directory.glob(pat))
        files = sorted(set(files))
        results = []
        for f in files:
            try:
                results.append(self.ingest_file(f, zone=zone))
            except Exception as exc:
                logger.exception("Fallo ingesta %s: %s", f, exc)
                results.append({"path": str(f), "error": str(exc)})
        # Persist manifest
        manifest = self.cfg.index_path / "manifest.json"
        manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        return results
