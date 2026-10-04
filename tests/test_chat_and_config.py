"""Seguimientos en chat, override de emergencia configurable, traces y caché de modelos."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from shiprag.api.app import create_app
from shiprag.core import model_cache
from shiprag.core.config import ChatConfig, PathsConfig, clear_config_cache, load_config
from shiprag.core.logging import write_trace
from shiprag.core.schemas import Zone
from shiprag.experts.router import QueryRouter
from shiprag.generation.condense import condense_query, is_followup


def _lite_cfg(tmp_path: Path):
    clear_config_cache()
    base = load_config(profile="lite")
    data = tmp_path / "data"
    cfg = base.model_copy(deep=True)
    cfg.paths = PathsConfig(
        data_dir=str(data),
        raw_dir=str(data / "raw"),
        index_dir=str(data / "indexes"),
        log_dir=str(data / "logs"),
        models_dir=str(tmp_path / "models"),
        sample_dir=str(Path(__file__).resolve().parents[1] / "data" / "sample"),
    )
    return cfg


def test_followup_detection():
    assert is_followup("¿y si no arranca?", 6)
    assert is_followup("¿por qué?", 6)
    assert is_followup("¿cuál es la presión de eso?", 6)
    assert not is_followup("prueba GMDSS", 6)
    assert not is_followup("alarma FO-12 del generador", 6)
    assert not is_followup("cómo se hace el relevo de guardia en el puente", 6)


def test_condense_heuristic_and_standalone():
    cfg = ChatConfig()
    turns = [
        ("user", "presión mínima de aire de arranque del generador"),
        ("assistant", "Según «Manual»: 25 bar\n---\nEstado: ok"),
        ("user", "¿y si no arranca?"),
    ]
    c = condense_query(turns, cfg)
    assert c.method == "heuristic"
    assert c.query.startswith("presión mínima de aire") and c.query.endswith("¿y si no arranca?")
    standalone = condense_query(turns[:2] + [("user", "prueba diaria del GMDSS")], cfg)
    assert standalone.method == "none" and standalone.query == "prueba diaria del GMDSS"
    off = condense_query(turns, ChatConfig(condense_followups=False))
    assert off.method == "none"


def test_condense_with_llm_and_fallback():
    class FakeLLM:
        def __init__(self, reply):
            self.reply = reply
            self.prompts = []

        def complete(self, messages, max_tokens=128, temperature=0.0):
            self.prompts.append(messages)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    turns = [("user", "presión mínima de aire de arranque"), ("user", "¿y si no arranca?")]
    llm = FakeLLM("¿Qué hacer si el generador no arranca con la presión mínima de aire?")
    c = condense_query(turns, ChatConfig(), llm=llm)
    assert c.method == "llm" and "generador no arranca" in c.query
    assert "última pregunta" in llm.prompts[0][1]["content"]
    broken = condense_query(turns, ChatConfig(), llm=FakeLLM(RuntimeError("caído")))
    assert broken.method == "heuristic"


def test_chat_followup_inherits_emergency(tmp_path: Path):
    cfg = _lite_cfg(tmp_path)
    client = TestClient(create_app(cfg))
    sample = Path(cfg.paths.sample_dir) / "emergencias_hombre_al_agua.txt"
    files = {"file": (sample.name, sample.read_bytes(), "text/plain")}
    assert client.post("/api/ingest", files=files).status_code == 200
    body = {
        "model": "shiprag",
        "messages": [
            {"role": "user", "content": "hombre al agua por estribor"},
            {"role": "assistant", "content": "Pasos del procedimiento…"},
            {"role": "user", "content": "¿y si no lo veo?"},
        ],
    }
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 200, r.text
    content = r.json()["choices"][0]["message"]["content"]
    assert "Consulta interpretada" in content
    assert "emergencias" in content  # zona usada: se mantiene el modo emergencia


def test_emergency_patterns_are_configurable():
    cfg = load_config(profile="lite").model_copy(deep=True)
    assert QueryRouter(cfg).route("plan de emergencia de la oficina").emergency is True
    cfg.router.emergency_patterns = []
    d = QueryRouter(cfg).route("plan de emergencia de la oficina")
    assert d.emergency is False and d.zones != [Zone.EMERGENCIAS]


def test_traces_jsonl_append_and_legacy_json(tmp_path: Path):
    cfg = _lite_cfg(tmp_path)
    p1 = write_trace({"query": "a"}, cfg)
    p2 = write_trace({"query": "b"}, cfg)
    assert p1 == p2 and p1.name.startswith("trace-") and p1.suffix == ".jsonl"
    rows = [json.loads(line) for line in p1.read_text(encoding="utf-8").splitlines()]
    assert [r["query"] for r in rows] == ["a", "b"] and all("ts" in r for r in rows)
    cfg.logging.trace_format = "json"
    p3 = write_trace({"query": "c"}, cfg)
    assert p3.suffix == ".json" and json.loads(p3.read_text(encoding="utf-8"))["query"] == "c"


def test_model_cache_loads_once():
    calls = []

    def factory():
        calls.append(1)
        return object()

    key = ("test-model", "ruta", "cpu")
    a = model_cache.get_or_create(key, factory)
    b = model_cache.get_or_create(key, factory)
    assert a is b and len(calls) == 1
