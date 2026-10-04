"""Preguntas de seguimiento en chat → consulta autónoma para la recuperación.

Sin esto, "¿y si no arranca?" se busca tal cual en el índice y no recupera nada.
Solo se reescribe lo que parece un seguimiento (marcador inicial, referencia
"eso/ese…" o pregunta muy corta); las preguntas autónomas pasan sin tocar y sin
coste de LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from shiprag.core.config import ChatConfig
from shiprag.generation.verifier import tokenize

logger = logging.getLogger("shiprag.condense")

FOLLOWUP_START_RE = re.compile(
    r"^\s*¿?\s*(y|e|pero|entonces|también|tambien|además|ademas|o\s+sea|"
    r"and|but|what\s+about|how\s+about)\b",
    re.I,
)
REFERENCE_RE = re.compile(
    r"\b(eso|esto|ese|esa|esos|esas|aquel|aquella|aquello|lo\s+mismo|el\s+mismo|"
    r"la\s+misma|el\s+anterior|la\s+anterior|ahí|allí|it|that|those)\b",
    re.I,
)
# Preguntas muy cortas y genéricas ("¿por qué?", "¿cómo?", "más detalles") dependen del turno anterior;
# las cortas con términos propios ("prueba GMDSS", "alarma FO-12") son autónomas
GENERIC_RE = re.compile(
    r"(por\s*qu[eé]|cu[aá]nto|cu[aá]ndo|c[oó]mo|d[oó]nde|cu[aá]l|qu[eé]|\bm[aá]s\b|detalles?|"
    r"ejemplos?|expl[ií]ca\w*|why|how|when|where|which|more|details?)",
    re.I,
)
# Pie que añade la API OpenAI-compat a cada respuesta: no aporta al contexto
FOOTER_RE = re.compile(r"\n---\n.*", re.S)

CONDENSE_SYSTEM_PROMPT = (
    "Reescribe la ÚLTIMA pregunta del usuario como una pregunta autónoma y completa, "
    "usando la conversación previa solo para resolver referencias (eso, el anterior, "
    "¿y si…?). No respondas a la pregunta y no añadas datos que no estén en la "
    "conversación. Devuelve solo la pregunta reescrita, en una línea, en el mismo idioma."
)


@dataclass
class CondensedQuery:
    query: str
    original: str
    method: str  # none | heuristic | llm
    previous: str = ""  # pregunta anterior usada como contexto


def is_followup(question: str, max_terms: int) -> bool:
    terms = tokenize(question)
    if FOLLOWUP_START_RE.match(question):
        return True
    if len(terms) <= 2 and (not terms or GENERIC_RE.search(question)):
        return True
    return bool(REFERENCE_RE.search(question)) and len(terms) <= max_terms


def _clean_rewrite(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line.strip().strip('"«»“”').strip()


def condense_query(
    turns: list[tuple[str, str]],
    cfg: ChatConfig,
    llm=None,
) -> CondensedQuery:
    """`turns`: (role, texto) en orden; el último turno de usuario es la pregunta.

    `llm`: objeto con `.complete(messages, max_tokens=, temperature=)` (LLMGenerator) o None.
    """
    user_idx = [i for i, (role, _) in enumerate(turns) if role == "user"]
    if not user_idx:
        return CondensedQuery(query="", original="", method="none")
    question = turns[user_idx[-1]][1].strip()
    previous_users = [turns[i][1].strip() for i in user_idx[:-1] if turns[i][1].strip()]
    if (
        not cfg.condense_followups
        or not previous_users
        or not is_followup(question, cfg.followup_max_terms)
    ):
        return CondensedQuery(query=question, original=question, method="none")

    previous = previous_users[-1]
    if llm is not None and cfg.use_llm_rewrite:
        history = turns[: user_idx[-1]][-(cfg.history_turns * 2) :]
        convo = []
        for role, text in history:
            if role not in {"user", "assistant"} or not text.strip():
                continue
            text = FOOTER_RE.sub("", text).strip()
            convo.append(f"{'Usuario' if role == 'user' else 'Asistente'}: {text[:400]}")
        convo.append(f"Usuario (última pregunta): {question}")
        try:
            rewritten = _clean_rewrite(
                llm.complete(
                    [
                        {"role": "system", "content": CONDENSE_SYSTEM_PROMPT},
                        {"role": "user", "content": "\n".join(convo)},
                    ],
                    max_tokens=96,
                    temperature=0.0,
                )
            )
            if 3 <= len(rewritten) <= 400 and not rewritten.upper().startswith("NO ENCONTRADO"):
                return CondensedQuery(
                    query=rewritten, original=question, method="llm", previous=previous
                )
        except Exception as exc:
            logger.warning("Reescritura con LLM falló (%s); heurística", exc)

    # Heurística: anteponer la pregunta anterior (mantiene sus términos y su criticidad)
    return CondensedQuery(
        query=f"{previous} {question}", original=question, method="heuristic", previous=previous
    )
