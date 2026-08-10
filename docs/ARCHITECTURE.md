# Guía técnica de decisiones (ShipRAG)

> Documentación completa: **[DOCUMENTACION.md](DOCUMENTACION.md)**  
> Novedades (router, NLI, DP, tiers): **[NOVEDADES.md](NOVEDADES.md)**

## Principio rector

En un buque, una respuesta inventada es peor que no responder.
Por eso el MVP **prioriza abstención y citas** sobre fluidez conversacional.

## Capas anti-alucinación

1. **Routing por zona** (embeddings + examples, o keywords en lite) reduce el espacio de búsqueda.
2. **Override duro de emergencia** → solo `emergencias` + extractivo.
3. **Híbrido BM25 + denso** cubre códigos técnicos y lenguaje natural.
4. **Rerank** empuja procedimientos numerados / warnings en emergencias.
5. **Generación extractiva** por defecto en criticidad alta.
6. **Grounding léxico** sobre la evidencia.
7. **NLI entailment** opcional (segunda capa del verifier).
8. **Umbrales** distintos normal vs emergencia.
9. **Trace persistente** en `data/logs/trace_*.json` para auditoría ISM.

## Por qué no “solo un LLM con los PDFs”

Un LLM puede resumir mal un SOPEP, reordenar pasos o mezclar versiones.
En emergencias el modo `extractive` / `citations_only` **pega el texto del manual**.

El LLM local (GGUF) es opcional. Si se activa, sigue obligado a anclarse a citas
y pasa por el verificador (léxico ± NLI); si falla grounding, se abstiene.

## Modelos recomendados (descarga en puerto)

Dos tiers explícitos (no son intercambiables por gusto):

| Tier | Perfil | Stack |
|---|---|---|
| Prototipo / banco de pruebas (~12GB VRAM) | `workstation` | e5-base + bge-reranker-base + Qwen2.5-7B |
| Producción a bordo | `server` | BGE-M3 + bge-reranker-v2-m3 + Qwen3-32B Q4_K_M |

`server` define `fallback_name_or_path` hacia el stack workstation si faltan pesos o falla la carga.

Colocar bajo `models/`:

```
models/embeddings/multilingual-e5-base/      # workstation
models/embeddings/bge-m3/                    # server
models/reranker/bge-reranker-base/           # workstation
models/reranker/bge-reranker-v2-m3/          # server
models/verifier/mDeBERTa-v3-base-mnli-xnli/  # NLI grounding (home+)
models/llm/*.gguf                            # opcional, llama.cpp
```

Sin ellos, el sistema opera con:
- `HashEmbedding` (tests / bootstrap)
- `LexicalReranker`
- `ExtractiveGenerator`
- verifier léxico (sin NLI)
- router por keywords

Esto permite CI y demos sin GPU ni descargas, pero **no es la calidad de producción**.

## Router

- `config/default.yaml` → `zones[].keywords` + `zones[].examples`
- `router.backend`: `auto` | `keywords` | `embedding` (`lite` fuerza `keywords`)
- Emergencia (patrón o flag): fuerza `emergencias`, independiente del embedding
- Zona DP: `posicionamiento_dinamico` (thrusters, watch circle, PRS, DGPS, …)

## Verifier

- Siempre: scores léxicos (`generation.grounding_threshold`, `retrieval.min_evidence_score`)
- Opcional: `models.verifier.backend: auto|nli` → entailment mDeBERTa
- Lite: `models.verifier.backend: lexical`

## OCR

Tesseract local (`spa+eng`). Planos P&ID densos seguirán siendo difíciles:
recomendación operativa — mantener versiones con capas de texto o anexar
tablas de referencias de válvulas/equipos como TXT/CSV indexables.

## Perfiles de hardware

| Perfil | Backends | Índice |
|---|---|---|
| `lite` | hash + lexical + extractivo + keywords | `data/indexes/lite` |
| `home` | ST pequeño + NLI si existe | `data/indexes/home` |
| `balanced` | e5-base + CE (GPU suave) | `data/indexes/balanced` |
| `workstation` | e5-base + CE base + Qwen2.5-7B (prototipo) | `data/indexes/workstation` |
| `server` | BGE-M3 + CE v2-m3 + Qwen3-32B (producción) | `data/indexes/server` |

Selección: `shiprag --profile lite …` o `SHIPRAG_PROFILE=lite`.
Los YAML viven en `config/profiles/` y se fusionan sobre `config/default.yaml`.

## Añadir una zona nueva

1. Añadir valor en `Zone` (`src/shiprag/core/schemas.py`).
2. Entrada en `config/default.yaml` con `keywords` + `examples`.
3. Hints/aliases en `src/shiprag/ingest/pipeline.py` si quieres inferencia automática.
4. Ingestar corpus con `--zone <id>` o sidecar `.meta.yaml`.

No hace falta tocar el orquestador: los índices son por carpeta de zona.

## Evaluación

| Fichero | Uso |
|---|---|
| `eval/golden_set.jsonl` | CI / regresión sintética |
| `eval/golden_set_crew_draft.jsonl` | Plantilla preguntas tripulación (revisar → fusionar) |

```bash
shiprag --profile lite eval eval/golden_set.jsonl
```
