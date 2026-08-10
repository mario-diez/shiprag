"""Extracción de PDF/imágenes con estructura de página.

Decisiones:
- PyMuPDF para texto + layout rápido.
- pdfplumber para tablas (mejor que fitz en muchos PDFs técnicos).
- OCR solo si la página tiene poco texto (escaneados/planos).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import ExtractedBlock
from shiprag.ingest.ocr import LocalOCR

logger = logging.getLogger("shiprag.ingest.pdf")

HEADING_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s+.{3,100}"
    r"|(?:\d+(?:\.\d+){0,4})\s+[A-ZÁÉÍÓÚÑ#].{3,80}"
    r"|(?:CAPÍTULO|CAPITULO|SECCIÓN|SECCION|ANEXO|PROCEDIMIENTO|CHECKLIST|"
    r"PROCEDURE|SECTION|CHAPTER)\b.{0,80}"
    r"|[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9\s\-/]{8,80}"
    r")$"
)
WARNING_RE = re.compile(
    r"\b(PELIGRO|WARNING|DANGER|PRECAUCIÓN|PRECAUCION|CAUTION|AVISO|IMPORTANT|"
    r"NO\s+OPERAR|PROHIBIDO)\b",
    re.IGNORECASE,
)
LIST_RE = re.compile(r"^\s*(?:\d+[\.\)]\s+|[-•▪]\s+|[a-z][\.\)]\s+)")


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if len(s) < 4 or len(s) > 120:
        return False
    if s.startswith("#"):
        return True
    return bool(HEADING_RE.match(s))


class DocumentExtractor:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self.ocr = LocalOCR(lang=self.cfg.ocr.lang, enabled=self.cfg.ocr.enabled)

    def extract(self, path: Path) -> list[ExtractedBlock]:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
            return self._extract_image(path)
        if suffix in {".txt", ".md"}:
            return self._extract_text(path)
        raise ValueError(f"Formato no soportado: {suffix}")

    def _extract_text(self, path: Path) -> list[ExtractedBlock]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        blocks: list[ExtractedBlock] = []
        current_section: str | None = None
        heading_path: list[str] = []
        buf: list[str] = []
        page = 1

        def flush() -> None:
            nonlocal buf
            if not buf:
                return
            joined = "\n".join(buf).strip()
            if joined:
                btype = "warning" if WARNING_RE.search(joined) else "paragraph"
                if any(LIST_RE.match(x) for x in buf):
                    btype = "list"
                blocks.append(
                    ExtractedBlock(
                        text=joined,
                        page=page,
                        section=current_section,
                        heading_path=list(heading_path),
                        block_type=btype,
                    )
                )
            buf = []

        for line in text.splitlines():
            if _looks_like_heading(line):
                flush()
                current_section = line.strip()
                heading_path = [current_section]
                blocks.append(
                    ExtractedBlock(
                        text=current_section,
                        page=page,
                        section=current_section,
                        heading_path=list(heading_path),
                        block_type="heading",
                    )
                )
            else:
                buf.append(line)
        flush()
        return blocks

    def _extract_image(self, path: Path) -> list[ExtractedBlock]:
        from PIL import Image

        img = Image.open(path)
        text = self.ocr.image_to_text(img).strip()
        if not text:
            return [
                ExtractedBlock(
                    text=f"[Imagen sin texto OCR: {path.name}]",
                    page=1,
                    section=None,
                    block_type="figure_caption",
                    is_ocr=True,
                )
            ]
        return [
            ExtractedBlock(
                text=text,
                page=1,
                section=None,
                block_type="ocr",
                is_ocr=True,
            )
        ]

    def _extract_pdf(self, path: Path) -> list[ExtractedBlock]:
        import fitz  # PyMuPDF
        import pdfplumber

        blocks: list[ExtractedBlock] = []
        current_section: str | None = None
        heading_path: list[str] = []

        # Tablas por página con pdfplumber
        tables_by_page: dict[int, list[str]] = {}
        try:
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    page_tables: list[str] = []
                    for table in page.extract_tables() or []:
                        rows = []
                        for row in table:
                            cells = [((c or "").strip()) for c in row]
                            if any(cells):
                                rows.append(" | ".join(cells))
                        if rows:
                            page_tables.append("\n".join(rows))
                    if page_tables:
                        tables_by_page[i] = page_tables
        except Exception as exc:
            logger.warning("pdfplumber falló en %s: %s", path, exc)

        doc = fitz.open(path)
        try:
            for i, page in enumerate(doc, start=1):
                text = (page.get_text("text") or "").strip()
                used_ocr = False
                if len(text) < self.cfg.ocr.min_text_chars_per_page:
                    # Página pobre en texto → OCR
                    pix = page.get_pixmap(dpi=200)
                    from PIL import Image
                    import io

                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    ocr_text = self.ocr.image_to_text(img).strip()
                    if len(ocr_text) > len(text):
                        text = ocr_text
                        used_ocr = True

                if text:
                    for para in re.split(r"\n\s*\n", text):
                        para = para.strip()
                        if not para:
                            continue
                        lines = [ln.rstrip() for ln in para.splitlines() if ln.strip()]
                        if len(lines) == 1 and _looks_like_heading(lines[0]):
                            current_section = lines[0].strip()
                            heading_path = [current_section]
                            blocks.append(
                                ExtractedBlock(
                                    text=current_section,
                                    page=i,
                                    section=current_section,
                                    heading_path=list(heading_path),
                                    block_type="heading",
                                    is_ocr=used_ocr,
                                )
                            )
                            continue
                        btype = "ocr" if used_ocr else "paragraph"
                        if WARNING_RE.search(para):
                            btype = "warning"
                        elif any(LIST_RE.match(ln) for ln in lines):
                            btype = "list"
                        blocks.append(
                            ExtractedBlock(
                                text=para,
                                page=i,
                                section=current_section,
                                heading_path=list(heading_path),
                                block_type=btype,
                                is_ocr=used_ocr,
                            )
                        )

                for table_text in tables_by_page.get(i, []):
                    blocks.append(
                        ExtractedBlock(
                            text=table_text,
                            page=i,
                            section=current_section,
                            heading_path=list(heading_path),
                            block_type="table",
                        )
                    )
        finally:
            doc.close()

        return blocks
