"""Caché de modelos pesados compartidos dentro del proceso.

Sin esto, cada experto de zona cargaba su propio cross-encoder (hasta 9 copias
en GPU) y cada Orchestrator volvía a cargar embedder, NLI y LLM.
La clave debe incluir todo lo que cambia el modelo (ruta, device, parámetros).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_CACHE: dict[tuple[Any, ...], Any] = {}
_LOCK = threading.RLock()


def get_or_create(key: tuple[Any, ...], factory: Callable[[], T]) -> T:
    """Devuelve la instancia cacheada para `key` o la crea una sola vez.

    Si `factory` lanza excepción no se cachea nada (el llamador hace fallback).
    """
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        obj = factory()
        _CACHE[key] = obj
        return obj


def clear() -> None:
    with _LOCK:
        _CACHE.clear()


def cached_keys() -> list[tuple[Any, ...]]:
    with _LOCK:
        return list(_CACHE)
