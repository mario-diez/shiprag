"""Logging estructurado local (sin telemetría externa)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config


def setup_logging(cfg: AppConfig | None = None) -> logging.Logger:
    cfg = cfg or load_config()
    ensure_runtime_dirs(cfg)
    logger = logging.getLogger("shiprag")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, cfg.logging.level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler(cfg.log_path / "shiprag.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def write_trace(trace: dict[str, Any], cfg: AppConfig | None = None) -> Path:
    """Persiste el razonamiento de recuperación para auditoría a bordo."""
    cfg = cfg or load_config()
    ensure_runtime_dirs(cfg)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = cfg.log_path / f"trace_{ts}.json"
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
