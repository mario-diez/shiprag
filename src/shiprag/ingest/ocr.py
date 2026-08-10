"""OCR local opcional (Tesseract). Nunca llama a servicios cloud."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger("shiprag.ocr")


class LocalOCR:
    """Wrapper fino sobre pytesseract. Si no está instalado, degrada con aviso."""

    def __init__(self, lang: str = "spa+eng", enabled: bool = True) -> None:
        self.lang = lang
        self.enabled = enabled
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is not None:
            return self._available
        if not self.enabled:
            self._available = False
            return False
        try:
            import pytesseract  # noqa: F401

            self._available = True
        except Exception:
            logger.warning(
                "OCR no disponible (instale pytesseract + tesseract-ocr). "
                "Las páginas escaneadas se indexarán vacías."
            )
            self._available = False
        return self._available

    def image_to_text(self, image: "Image.Image") -> str:
        if not self.available:
            return ""
        import pytesseract

        try:
            return pytesseract.image_to_string(image, lang=self.lang) or ""
        except Exception as exc:  # pragma: no cover - depende del binario
            logger.warning("OCR falló: %s", exc)
            return ""
