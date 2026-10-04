"""Generación con LLM sin pesos reales: backends simulados (llama.cpp / HTTP)."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from shiprag.core.config import LLMConfig, load_config
from shiprag.core.schemas import AnswerStatus, Chunk, ResponseMode, ScoredChunk
from shiprag.generation.generator import (
    LLMGenerator,
    LlamaCppChat,
    OpenAICompatChat,
    build_context,
    build_generator,
    clean_llm_output,
)
from shiprag.generation.verifier import AnswerVerifier


def _items(n: int = 3, size: int = 300) -> list[ScoredChunk]:
    out = []
    for i in range(n):
        text = f"Fuente {i + 1}: la presión mínima de aire de arranque es {20 + i} bar. " + (
            "detalle " * (size // 8)
        )
        ch = Chunk(
            chunk_id=f"c{i}",
            doc_id=f"d{i}",
            text=text,
            page_start=i + 1,
            page_end=i + 1,
            title=f"Manual {i + 1}",
        )
        out.append(ScoredChunk(chunk=ch, score=1.0 - i * 0.1, rrf_score=0.03))
    return out


class FakeChat:
    name = "fake"
    model = "fake-model"

    def __init__(self, reply: str | Exception = "") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, *, temperature, max_tokens, stop=None) -> str:
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_prompt_uses_chat_roles_and_full_chunk_text():
    chat = FakeChat("La presión mínima es 20 bar [1].")
    gen = LLMGenerator(chat, LLMConfig(enabled=True, n_ctx=8192, max_tokens=256))
    items = _items(2, size=900)
    resp = gen.generate("presión mínima de aire", items, ResponseMode.GENERATIVE)
    system, user = chat.calls[0]
    assert system["role"] == "system" and "EXCLUSIVAMENTE" in system["content"]
    assert user["role"] == "user"
    # Texto completo del chunk (antes se recortaba a 500 caracteres)
    assert items[0].chunk.text.strip() in user["content"]
    assert '<fuente id="1"' in user["content"] and '<fuente id="2"' in user["content"]
    assert resp.mode_used == ResponseMode.GENERATIVE
    assert resp.status == AnswerStatus.OK
    assert len(resp.citations) == 2
    assert resp.generation_info["model"] == "fake-model"


def test_context_budget_limits_sources():
    items = _items(5, size=2000)
    context, used = build_context(items, max_chars=2500, max_sources=5)
    assert len(used) == 1 and len(context) <= 2600
    context, used = build_context(items, max_chars=100_000, max_sources=3)
    assert len(used) == 3


def test_clean_output_strips_think_bad_refs_and_loops():
    raw = (
        "<think>razonamiento interno</think>RESPUESTA: 25 bar [1] [7] [n]\n\n"
        "RESPUESTA: NO ENCONTRADO\n\nRESPUESTA: 25 bar [1] [7] [n]\n"
        "RESPUESTA: NO ENCONTRADO\n"
    )
    text = clean_llm_output(raw, n_sources=2)
    assert "<think>" not in text and "razonamiento" not in text
    assert "[7]" not in text and "[n]" not in text and "[1]" in text
    assert text.count("NO ENCONTRADO") <= 1
    assert text.count("25 bar") == 1 and "RESPUESTA" not in text
    assert clean_llm_output("<think>sin cerrar, cortado por max_tokens", 1) == ""


def test_llm_not_found_becomes_abstention():
    chat = FakeChat("NO ENCONTRADO")
    cfg = load_config(profile="lite")
    gen = LLMGenerator(chat, LLMConfig(enabled=True))
    items = _items(2)
    resp = gen.generate("presión mínima de aire de arranque", items, ResponseMode.GENERATIVE)
    assert resp.status == AnswerStatus.NOT_FOUND
    verified = AnswerVerifier(cfg).verify(
        "presión mínima de aire de arranque", resp, items, emergency=False,
        mode=ResponseMode.GENERATIVE,
    )
    assert verified.accepted is False
    assert verified.response.status == AnswerStatus.NOT_FOUND


@pytest.mark.parametrize("reply", ["[1]", "  [2] . ", ""])
def test_contentless_answer_degrades_instead_of_passing_verifier(reply):
    """Visto con Qwen2.5-1.5B: responder solo "[1]" daba grounding 1.0 y estado ok."""
    gen = LLMGenerator(FakeChat(reply), LLMConfig(enabled=True))
    resp = gen.generate("zona de snap-back en amarre", _items(2), ResponseMode.GENERATIVE)
    assert resp.mode_used == ResponseMode.SEMI
    assert "fallback" in resp.generation_info
    assert resp.answer.startswith("Según")


@pytest.mark.parametrize("error", [httpx.ConnectError("caído"), RuntimeError("boom")])
def test_backend_failure_degrades_to_semi_extractive(error):
    gen = LLMGenerator(FakeChat(error), LLMConfig(enabled=True))
    resp = gen.generate("presión mínima de aire", _items(2), ResponseMode.GENERATIVE)
    assert resp.mode_used == ResponseMode.SEMI
    assert resp.citations
    assert "fallback" in resp.generation_info


def test_context_overflow_retries_with_smaller_budget():
    class Overflow(FakeChat):
        def chat(self, messages, **kw):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise ValueError("Requested tokens (5000) exceed context window of 4096")
            return "20 bar [1]"

    chat = Overflow()
    gen = LLMGenerator(chat, LLMConfig(enabled=True, n_ctx=4096, max_tokens=512))
    resp = gen.generate("presión", _items(5, size=2000), ResponseMode.GENERATIVE)
    assert len(chat.calls) == 2
    assert len(chat.calls[1][1]["content"]) < len(chat.calls[0][1]["content"])
    assert resp.mode_used == ResponseMode.GENERATIVE


def test_openai_compat_backend_payload_and_auth(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "20 bar [1]"}}]}
        )

    chat = OpenAICompatChat(
        "http://llm.local:8000/v1", "qwen-test", api_key="k-123",
        transport=httpx.MockTransport(handler),
    )
    gen = LLMGenerator(chat, LLMConfig(enabled=True, temperature=0.2, max_tokens=99))
    resp = gen.generate("presión mínima", _items(1), ResponseMode.GENERATIVE)
    assert seen["url"] == "http://llm.local:8000/v1/chat/completions"
    assert seen["auth"] == "Bearer k-123"
    assert seen["body"]["model"] == "qwen-test"
    assert seen["body"]["max_tokens"] == 99 and seen["body"]["temperature"] == 0.2
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert resp.answer == "20 bar [1]"


def test_build_generator_openai_compat_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("SHIPRAG_TEST_LLM_KEY", "secreto")
    cfg = load_config(profile="lite").model_copy(deep=True)
    cfg.models.llm = LLMConfig(
        enabled=True, backend="openai_compat", base_url="http://127.0.0.1:9/v1",
        model="m", api_key_env="SHIPRAG_TEST_LLM_KEY",
    )
    gen = build_generator(cfg)
    assert isinstance(gen, LLMGenerator)
    assert gen.backend._client.headers["authorization"] == "Bearer secreto"
    # Sin base_url → extractivo (no rompe)
    cfg.models.llm = LLMConfig(enabled=True, backend="openai_compat")
    assert not isinstance(build_generator(cfg), LLMGenerator)


def test_llama_cpp_calls_are_serialized():
    """llama_cpp.Llama no es thread-safe: el backend debe atender de una en una."""

    class FakeLlama:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        def create_chat_completion(self, **kw):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            self.active -= 1
            return {"choices": [{"message": {"content": "ok"}}]}

    backend = LlamaCppChat.__new__(LlamaCppChat)
    backend.llm = FakeLlama()
    backend.model = "fake.gguf"
    backend._lock = threading.Lock()
    threads = [
        threading.Thread(
            target=backend.chat, args=([{"role": "user", "content": "x"}],),
            kwargs={"temperature": 0.0, "max_tokens": 8},
        )
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert backend.llm.max_active == 1
