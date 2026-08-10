"""Empaqueta un zip listo para copiar al PC de casa (sin .venv ni índices)."""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "dist"

SKIP_DIRS = {
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "data/indexes",
    "data/logs",
    "data/raw",
    "dist",
    "models",  # pesos grandes; se descargan aparte
}
SKIP_SUFFIXES = {".pyc", ".gguf", ".pt", ".bin", ".safetensors"}


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    for d in SKIP_DIRS:
        if rel == d or rel.startswith(d + "/"):
            return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    if path.name == ".DS_Store":
        return True
    return False


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    zip_path = OUT_DIR / f"shiprag-home-kit-{ts}.zip"
    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if should_skip(path):
                continue
            arc = path.relative_to(ROOT).as_posix()
            zf.write(path, arcname=f"shiprag/{arc}")
            count += 1
        # Nota de arranque en la raíz del zip
        zf.writestr(
            "shiprag/LEE_PRIMERO.txt",
            "1) Leer QUICKSTART_CASA.md\n"
            "2) Linux: bash scripts/start_lite.sh\n"
            "3) Windows: scripts\\start_lite.bat\n",
        )
    print(f"OK {zip_path} ({count} ficheros)")
    print(f"Tamaño: {zip_path.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
