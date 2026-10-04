"""Pipeline de ingesta offline: extract → chunk → index."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config
from shiprag.core.schemas import (
    Criticality,
    DocType,
    DocumentMeta,
    Zone,
)
from shiprag.ingest.chunker import StructuralChunker
from shiprag.ingest.pdf_extractor import SUPPORTED_EXTENSIONS, DocumentExtractor
from shiprag.ingest.registry import DocumentRegistry
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


# Subir si cambia la lógica de extracción/chunking: fuerza reindexar todo en la siguiente ingesta
INGEST_FORMAT_VERSION = "2"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def source_uri_for(path: Path) -> str:
    """Identidad estable de un fichero de origen (ruta absoluta normalizada)."""
    return Path(path).resolve().as_posix()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


class IngestPipeline:
    """extract → chunk → index, con ciclo de vida de documentos.

    - Identidad: `doc_id` explícito del sidecar, o el que ya tenía ese fichero de
      origen, o el nombre del fichero (con sufijo si otro fichero ya lo usa).
    - Reingesta: sustituye la versión anterior (no deja chunks huérfanos).
    - Sin cambios (misma huella) → se omite; `force=True` reindexa igualmente.
    """

    def __init__(self, cfg: AppConfig | None = None, index: HybridIndex | None = None) -> None:
        self.cfg = cfg or load_config()
        ensure_runtime_dirs(self.cfg)
        self.extractor = DocumentExtractor(self.cfg)
        self.chunker = StructuralChunker(self.cfg)
        self.index = index or HybridIndex(self.cfg)
        self.registry = DocumentRegistry(self.cfg.index_path / "documents")
        self._lock = threading.RLock()

    def _resolve_doc_id(self, path: Path, sidecar: dict, source_uri: str, content_hash: str) -> str:
        if sidecar.get("doc_id"):
            return _slug(str(sidecar["doc_id"]))
        known = self.registry.find_by_source(source_uri)
        if known:
            return known
        base = _slug(path.stem)
        other = self.registry.get(base)
        if other is None or not other.source_uri:
            # Libre, o entrada de un índice anterior a este registro: se reclama
            return base
        if other.content_hash == content_hash and not Path(other.source_uri).exists():
            # Mismo contenido y el original ya no está: el fichero se ha movido
            return base
        # Mismo nombre de fichero en otra carpeta: no pisar el otro documento
        return f"{base}-{hashlib.sha1(source_uri.encode('utf-8')).hexdigest()[:8]}"

    def _fingerprint(self, content_hash: str, sidecar: dict, overrides: dict, copy: bool) -> str:
        payload = {
            "format": INGEST_FORMAT_VERSION,
            "content": content_hash,
            "sidecar": sidecar,
            "overrides": overrides,
            "copy_to_raw": copy,
            "chunking": self.cfg.chunking.model_dump(),
            "ocr": [self.cfg.ocr.enabled, self.cfg.ocr.lang],
            "embedder": self.index.embedder.name,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _remove_raw_copy(self, meta: DocumentMeta, keep: str | None = None) -> None:
        """Borra la copia en data/raw de una versión anterior.

        Solo lo que ShipRAG gestiona: copias que hizo la ingesta (`source_path` ≠ origen) y
        subidas por API (`data/raw/uploads`). Nunca un original ingerido en su sitio.
        """
        source_path = meta.source_path
        if not source_path or source_path == keep:
            return
        p = Path(source_path)
        if not p.exists() or not _is_within(p, self.cfg.raw_path):
            return
        uploaded = _is_within(p, self.cfg.raw_path / "uploads")
        # Entradas de índices antiguos (sin source_uri): no se sabe si es copia → se deja
        is_original = not meta.source_uri or source_uri_for(p) == meta.source_uri
        if is_original and not uploaded:
            return
        p.unlink(missing_ok=True)
        try:
            if p.parent != self.cfg.raw_path and not any(p.parent.iterdir()):
                p.parent.rmdir()
        except OSError:
            pass

    def ingest_file(
        self,
        path: Path,
        *,
        zone: Zone | None = None,
        doc_type: DocType | None = None,
        title: str | None = None,
        version: str = "1.0",
        language: str = "es",
        copy_to_raw: bool | None = None,
        force: bool = False,
    ) -> dict:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Formato no soportado: {path.suffix or path.name}")
        copy = self.cfg.ingest.copy_to_raw if copy_to_raw is None else copy_to_raw

        source_uri = source_uri_for(path)
        sidecar = _load_sidecar_meta(path)
        content_hash = _sha256_file(path)
        overrides = {
            "zone": zone.value if zone else None,
            "doc_type": doc_type.value if doc_type else None,
            "title": title,
            "version": version,
            "language": language,
        }

        with self._lock:
            doc_id = self._resolve_doc_id(path, sidecar, source_uri, content_hash)
            fingerprint = self._fingerprint(content_hash, sidecar, overrides, copy)
            prev = self.registry.get(doc_id)
            if (
                prev is not None
                and prev.source_uri
                and prev.source_uri != source_uri
                and Path(prev.source_uri).exists()
            ):
                logger.warning(
                    "doc_id=%s lo declaran %s y %s; esta ingesta sustituye a la anterior",
                    doc_id,
                    prev.source_uri,
                    source_uri,
                )
            if (
                not force
                and prev is not None
                and prev.ingest_fingerprint == fingerprint
                and prev.source_uri == source_uri
            ):
                logger.info("Sin cambios %s (doc_id=%s) → omitido", path.name, doc_id)
                return {
                    "doc_id": doc_id,
                    "title": prev.title,
                    "zone": prev.zone.value,
                    "doc_type": prev.doc_type.value,
                    "criticality": prev.criticality.value,
                    "chunks": prev.chunk_count,
                    "source_path": prev.source_path,
                    "action": "unchanged",
                }

            dest = path
            if copy:
                # Subcarpeta por doc_id: dos "manual.pdf" de carpetas distintas no se pisan
                dest_dir = self.cfg.raw_path / (zone.value if zone else "inbox") / doc_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / path.name
                if path.resolve() != dest.resolve():
                    shutil.copy2(path, dest)

            blocks = self.extractor.extract(dest)
            sample_text = " ".join(b.text for b in blocks[:8]) + " " + path.name
            z = infer_zone(sample_text, zone or _zone_from_sidecar(sidecar))
            dt = infer_doc_type(sample_text, doc_type or _type_from_sidecar(sidecar))
            crit = (
                Criticality(sidecar["criticality"])
                if sidecar.get("criticality")
                else infer_criticality(z, dt)
            )
            meta = DocumentMeta(
                doc_id=doc_id,
                title=title or sidecar.get("title") or path.stem.replace("_", " "),
                source_path=str(dest),
                version=str(sidecar.get("version") or version),
                zone=z,
                doc_type=dt,
                language=sidecar.get("language") or language,
                criticality=crit,
                tags=list(sidecar.get("tags") or []),
                extra={k: v for k, v in sidecar.items() if k not in {
                    "doc_id", "title", "version", "zone", "doc_type", "language", "criticality", "tags"
                }},
                source_uri=source_uri,
                content_hash=content_hash,
                ingest_fingerprint=fingerprint,
                ingested_at=_now_iso(),
            )
            chunks = self.chunker.chunk(meta, blocks)
            meta.chunk_count = len(chunks)
            removed, added = self.index.replace_document(doc_id, chunks, zone=z)
            self.registry.put(meta)
            if prev is not None:
                self._remove_raw_copy(prev, keep=str(dest))

        action = "created" if prev is None else "updated"
        logger.info(
            "Ingestado %s → %d chunks (%s, %d sustituidos; zona=%s tipo=%s criticidad=%s)",
            path.name,
            added,
            action,
            removed,
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
            "chunks": added,
            "blocks": len(blocks),
            "source_path": str(dest),
            "action": action,
            "chunks_replaced": removed,
        }

    def ingest_dir(
        self,
        directory: Path,
        *,
        zone: Zone | None = None,
        recursive: bool = True,
        force: bool = False,
        prune: bool = False,
        copy_to_raw: bool | None = None,
    ) -> list[dict]:
        """Ingesta un directorio. `prune=True` además retira del índice los documentos
        que procedían de este directorio y cuyo fichero ya no existe (sincronización)."""
        directory = Path(directory)
        candidates = directory.rglob("*") if recursive else directory.glob("*")
        files = sorted(
            p
            for p in candidates
            if p.is_file()
            and p.suffix.lower() in SUPPORTED_EXTENSIONS
            and not p.name.startswith(("~$", "."))
        )
        results: list[dict] = []
        # Un solo rebuild BM25 + persistencia al final del lote (antes: uno por fichero)
        with self.index.batch():
            for f in files:
                try:
                    results.append(
                        self.ingest_file(f, zone=zone, force=force, copy_to_raw=copy_to_raw)
                    )
                except Exception as exc:
                    logger.exception("Fallo ingesta %s: %s", f, exc)
                    results.append({"path": str(f), "error": str(exc), "action": "error"})
            if prune:
                results.extend(self.prune(directory))
        manifest = self.cfg.index_path / "manifest.json"
        manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        return results

    def prune(self, directory: Path) -> list[dict]:
        """Retira documentos cuyo original estaba bajo `directory` y ya no existe."""
        root = source_uri_for(directory).rstrip("/") + "/"
        out = []
        with self.index.batch():
            for meta in self.registry.all():
                if not meta.source_uri.startswith(root) or Path(meta.source_uri).exists():
                    continue
                res = self.delete_document(meta.doc_id)
                res["action"] = "pruned"
                out.append(res)
        return out

    def delete_document(self, doc_id: str) -> dict:
        """Elimina un documento del índice y del registro. KeyError si no existe."""
        with self._lock:
            meta = self.registry.get(doc_id)
            removed = self.index.delete_document(doc_id)
            if meta is None and removed == 0:
                raise KeyError(doc_id)
            self.registry.remove(doc_id)
            if meta is not None:
                self._remove_raw_copy(meta)
        logger.info("Documento %s eliminado (%d chunks)", doc_id, removed)
        return {
            "doc_id": doc_id,
            "title": meta.title if meta else "",
            "chunks_removed": removed,
            "action": "deleted",
        }

    def list_documents(self) -> list[DocumentMeta]:
        return self.registry.all()
