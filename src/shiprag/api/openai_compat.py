"""API compatible con OpenAI para conectar Open WebUI (u otros frontends).

Open WebUI → POST /v1/chat/completions → ShipRAG orchestrator.

No usa un LLM cloud: reutiliza el pipeline extractivo/abstención de ShipRAG.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from shiprag import __version__
from shiprag.core.schemas import QueryRequest, QueryResponse, ResponseMode, Zone


class ChatMessage(BaseModel):
    role: str
    content: str | list[Any] = ""


class ChatCompletionRequest(BaseModel):
    model: str = "shiprag"
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


def _content_to_text(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(p for p in parts if p)


def _extract_user_query(messages: list[ChatMessage]) -> str:
    """Toma el último mensaje de usuario; concatena contexto user reciente si ayuda."""
    users = [m for m in messages if m.role == "user"]
    if not users:
        # a veces llega como system-only; usar el último no-system
        others = [m for m in messages if m.role != "system"]
        if not others:
            return ""
        return _content_to_text(others[-1].content).strip()
    return _content_to_text(users[-1].content).strip()


def _model_to_query_flags(model: str) -> dict[str, Any]:
    """Mapea nombre de modelo Open WebUI → flags ShipRAG."""
    name = (model or "shiprag").lower().strip()
    emergency = any(x in name for x in ("emergency", "emergencia", "critical", "mob", "sopep"))
    mode = ResponseMode.AUTO
    if "citas" in name or "citations" in name or "emergency" in name or "emergencia" in name:
        mode = ResponseMode.CITATIONS_ONLY
        emergency = True
    elif "extract" in name:
        mode = ResponseMode.EXTRACTIVE
    return {"emergency": emergency, "mode": mode, "model_id": name or "shiprag"}


def _format_answer(resp: QueryResponse) -> str:
    """Texto que verá el usuario en Open WebUI (incluye citas visibles)."""
    lines: list[str] = []
    lines.append(resp.answer.strip())
    lines.append("")
    lines.append("---")
    lines.append(
        f"Estado: `{resp.status.value}` · confianza: `{resp.confidence:.3f}` · "
        f"modo: `{resp.mode_used.value}` · zonas: `{', '.join(resp.zones_used) or '-'}`"
    )
    if resp.clarification_question:
        lines.append(f"Clarificación: {resp.clarification_question}")
    if resp.conflicts:
        lines.append("")
        lines.append("⚠️ Conflictos detectados entre documentos:")
        for c in resp.conflicts:
            lines.append(f"- {c.detail}")
    if resp.citations:
        lines.append("")
        lines.append("### Fuentes")
        for i, c in enumerate(resp.citations, 1):
            sec = f" · § {c.section}" if c.section else ""
            pages = (
                f"pág. {c.page_start}"
                if c.page_start == c.page_end
                else f"pág. {c.page_start}–{c.page_end}"
            )
            lines.append(
                f"**[{i}] {c.title}** v{c.version} · {pages}{sec} · score {c.score:.3f}"
            )
            lines.append(f"> {c.quote[:400]}{'…' if len(c.quote) > 400 else ''}")
            lines.append("")
    lines.append("_ShipRAG offline — abstenerse > inventar._")
    return "\n".join(lines).strip()


def _completion_payload(model: str, content: str) -> dict[str, Any]:
    created = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": max(1, len(content.split())),
            "total_tokens": max(1, len(content.split())),
        },
    }


def _stream_sse(model: str, content: str):
    """Streaming SSE mínimo compatible con clientes OpenAI/Open WebUI."""
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    # primer chunk con role
    head = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(head, ensure_ascii=False)}\n\n"
    # trocear contenido para que la UI vaya pintando
    step = 240
    for i in range(0, len(content), step):
        piece = content[i : i + step]
        chunk = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    done = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def build_openai_router(get_orch: Callable) -> APIRouter:
    router = APIRouter(tags=["openai-compat"])

    @router.get("/v1/models")
    @router.get("/models")
    def list_models() -> dict[str, Any]:
        created = int(time.time())
        models = [
            ("shiprag", "ShipRAG (auto / extractivo)"),
            ("shiprag-emergency", "ShipRAG emergencia · solo citas"),
            ("shiprag-extractive", "ShipRAG extractivo"),
        ]
        return {
            "object": "list",
            "data": [
                {
                    "id": mid,
                    "object": "model",
                    "created": created,
                    "owned_by": "shiprag",
                    "root": mid,
                    "parent": None,
                    "description": desc,
                }
                for mid, desc in models
            ],
        }

    @router.get("/v1/")
    @router.get("/v1")
    def v1_root() -> dict[str, str]:
        return {
            "service": "shiprag-openai-compat",
            "version": __version__,
            "docs": "Usa /v1/models y /v1/chat/completions desde Open WebUI",
        }

    @router.post("/v1/chat/completions")
    @router.post("/chat/completions")
    async def chat_completions(body: ChatCompletionRequest, request: Request):
        query = _extract_user_query(body.messages)
        if not query:
            raise HTTPException(400, "No hay mensaje de usuario en messages[]")

        flags = _model_to_query_flags(body.model)
        # Heurística extra: si el system prompt habla de emergencia
        for m in body.messages:
            if m.role == "system":
                sys_txt = _content_to_text(m.content).lower()
                if any(k in sys_txt for k in ("emergencia", "emergency", "solo citas", "citations only")):
                    flags["emergency"] = True
                    if flags["mode"] == ResponseMode.AUTO:
                        flags["mode"] = ResponseMode.CITATIONS_ONLY

        req = QueryRequest(
            query=query,
            mode=flags["mode"],
            emergency=bool(flags["emergency"]),
        )
        orch = get_orch()
        resp: QueryResponse = orch.query(req)
        content = _format_answer(resp)
        model_id = body.model or "shiprag"

        if body.stream:
            return StreamingResponse(
                _stream_sse(model_id, content),
                media_type="text/event-stream",
            )
        return JSONResponse(_completion_payload(model_id, content))

    return router
