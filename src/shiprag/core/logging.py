"""Logging estructurado local (sin telemetría externa)."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shiprag.core.config import AppConfig, ensure_runtime_dirs, load_config

_TRACE_LOCK = threading.Lock()
_LAST_PRUNE: dict[str, str] = {}


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


def _prune_traces(log_dir: Path, retention_days: int, today: str) -> None:
    """Borra traces antiguos como mucho una vez al día por directorio."""
    key = str(log_dir)
    if retention_days <= 0 or _LAST_PRUNE.get(key) == today:
        return
    _LAST_PRUNE[key] = today
    cutoff = time.time() - retention_days * 86400
    for pattern in ("trace-*.jsonl", "trace_*.json"):
        for p in log_dir.glob(pattern):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass


def write_trace(trace: dict[str, Any], cfg: AppConfig | None = None) -> Path:
    """Persiste el razonamiento de recuperación para auditoría.

    `jsonl` (por defecto): una línea por consulta en `trace-YYYYMMDD.jsonl`, apto
    para miles de consultas al día. `json`: un fichero por consulta (formato anterior).
    """
    cfg = cfg or load_config()
    ensure_runtime_dirs(cfg)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y%m%d")
    with _TRACE_LOCK:
        if (cfg.logging.trace_format or "jsonl").lower() == "json":
            path = cfg.log_path / f"trace_{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
            path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path = cfg.log_path / f"trace-{today}.jsonl"
            record = {"ts": now.isoformat().replace("+00:00", "Z"), **trace}
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        _prune_traces(cfg.log_path, cfg.logging.trace_retention_days, today)
    return path
