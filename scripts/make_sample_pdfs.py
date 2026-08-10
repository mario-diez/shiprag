"""Genera PDFs de ejemplo a partir de los TXT sample (para probar ingesta PDF)."""

from __future__ import annotations

from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
OUT = SAMPLE / "pdf"


def txt_to_pdf(src: Path, dest: Path) -> None:
    doc = fitz.open()
    text = src.read_text(encoding="utf-8")
    # Páginas ~3500 chars
    chunk = 3500
    parts = [text[i : i + chunk] for i in range(0, len(text), chunk)] or [text]
    for part in parts:
        page = doc.new_page()
        rect = fitz.Rect(50, 50, 545, 792)
        page.insert_textbox(rect, part, fontsize=10, fontname="helv")
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    doc.close()


def main() -> None:
    n = 0
    for src in sorted(SAMPLE.glob("*.txt")):
        dest = OUT / f"{src.stem}.pdf"
        txt_to_pdf(src, dest)
        print(f"OK {dest.relative_to(ROOT)}")
        n += 1
    print(f"{n} PDFs en {OUT}")


if __name__ == "__main__":
    main()
