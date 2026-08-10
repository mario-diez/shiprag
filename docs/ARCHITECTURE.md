# Guía técnica de decisiones (ShipRAG)

> Para la documentación completa (montaje, inventario, gaps y roadmap):  
> **[DOCUMENTACION.md](DOCUMENTACION.md)**

## Principio rector

En un buque, una respuesta inventada es peor que no responder.
Por eso el MVP **prioriza abstención y citas** sobre fluidez conversacional.

## Capas anti-alucinación

1. **Routing por zona** reduce el espacio de búsqueda.
2. **Híbrido BM25 + denso** cubre códigos técnicos y lenguaje natural.
3. **Rerank** empuja procedimientos numerados / warnings en emergencias.
4. **Generación extractiva** por defecto en criticidad alta.
5. **Grounding score** lexical sobre la evidencia.
6. **Umbrales** distintos normal vs emergencia.
7. **Trace persistente** en `data/logs/trace_*.json` para auditoría ISM.

## Por qué no “solo un LLM con los PDFs”

Un LLM puede resumir mal un SOPEP, reordenar pasos o mezclar versiones.
En emergencias el modo `extractive` / `citations_only` **pega el texto del manual**.

El LLM local (GGUF) es opcional y está desactivado por defecto. Si se activa,
sigue obligado a anclarse a citas y pasa por el verificador; si falla grounding,
se abstiene.

## Modelos recomendados (descarga en puerto)

Colocar bajo `models/`:

```
models/embeddings/multilingual-e5-small/   # sentence-transformers
models/reranker/bge-reranker-base/         # CrossEncoder
models/llm/*.gguf                          # opcional, llama.cpp
```

Sin ellos, el sistema opera con:
- `HashEmbedding` (tests / bootstrap)
- `LexicalReranker`
- `ExtractiveGenerator`

Esto permite CI y demos sin GPU ni descargas, pero **no es la calidad de producción**.

## OCR

Tesseract local (`spa+eng`). Planos P&ID densos seguirán siendo difíciles:
recomendación operativa — mantener versiones con capas de texto o anexar
tablas de referencias de válvulas/equipos como TXT/CSV indexables.

## Perfiles de hardware

No hace falta un servidor para empezar:

| Perfil | Backends | Índice |
|---|---|---|
| `lite` | hash + lexical + extractivo | `data/indexes/lite` |
| `home` | ST pequeño si existe, si no fallback | `data/indexes/home` |
| `server` | ST base + CE + LLM opcional | `data/indexes/server` |

Selección: `shiprag --profile lite …` o `SHIPRAG_PROFILE=lite`.
Los YAML viven en `config/profiles/` y se fusionan sobre `config/default.yaml`.


1. Añadir zona en `config/default.yaml`.
2. Ingestar corpus con `--zone <id>`.
3. Opcionalmente ajustar keywords del router y `default_response_mode`.

No hace falta tocar el orquestador: los índices son por carpeta de zona.
