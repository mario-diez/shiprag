# Novedades ShipRAG

Documento de referencia de las mejoras respecto al MVP inicial (lo más reciente primero).
Complementa [ARCHITECTURE.md](ARCHITECTURE.md) y [DOCUMENTACION.md](DOCUMENTACION.md).

---

# 2026-10-04 — Fundamentos para escalar (rama `feat/fundamentos-escalado`)

Fase 1 del [plan de empresa](PLAN_EMPRESA.md). Motivo y mediciones: [AUDITORIA_ESCALADO.md](AUDITORIA_ESCALADO.md).
Todo lo relativo al LLM: [LLM.md](LLM.md).

## Resumen

| Pieza | Antes | Ahora |
|---|---|---|
| Reingesta de un documento modificado | Los chunks viejos **se quedaban** junto a los nuevos | Sustituye la versión anterior (BM25 + vectores, zona + general) |
| Documento sin cambios | Se reprocesaba entero | Se omite (huella de contenido + metadatos + config); `--force` para forzar |
| Borrar / sincronizar | No había | `shiprag delete <doc_id>`, `DELETE /api/documents/{doc_id}`, `ingest <dir> --prune` |
| Mismo nombre en dos carpetas | Se pisaban (`doc_id` = nombre; copia en `data/raw/inbox/<nombre>`) | `doc_id` estable por fichero de origen, con sufijo si colisiona; copia en `data/raw/<zona>/<doc_id>/` |
| Embeddings por chunk | 2 cálculos (zona + espejo general) | 1 |
| BM25 al ingerir un directorio | Reconstrucción completa por **cada** fichero | Una por lote; lecturas sobre snapshot; escritura atómica |
| Modelos en memoria | Reranker cargado por zona; todo recargado por orquestador | Una carga por proceso (`core/model_cache.py`) |
| Subida por API | Ruta del cliente sin sanear, sin límite, sin auth | Nombre saneado, tipos permitidos, `max_upload_mb`, token de administración |
| Concurrencia | Ingesta y chat bloqueaban el event loop; LLM sin bloqueo | Trabajo pesado en hilos; llama.cpp serializado; init sin carreras |
| LLM | Prompt crudo, citas recortadas a 500 chars, solo llama.cpp en proceso | Plantilla de chat, evidencia completa con presupuesto, limpieza, backend HTTP (vLLM/Ollama/nube) |
| Chat (Open WebUI) | Solo el último mensaje | Preguntas de seguimiento reescritas; emergencia heredada |
| Override de emergencia | Lista fija en código | `router.emergency_patterns` (vacío = desactivado) |
| Traces | Un JSON por consulta, sin retención | `trace-YYYYMMDD.jsonl` + `logging.trace_retention_days` |
| Tests | 13 | 57 |

## Notas de migración (léelas antes de actualizar)

1. **La primera ingesta tras actualizar reindexa todo una vez**: los índices antiguos no tienen
   huella, así que cada documento se procesa de nuevo y **sustituye** a su versión antigua (no se
   duplica). A partir de ahí, solo cambia lo que cambia.
2. **`doc_id` con sufijo en colisiones**: si dos ficheros distintos tenían el mismo nombre (en el
   corpus de ejemplo, `x.txt` y `pdf/x.pdf`), el segundo pasa a `x-<hash8>`. Antes compartían
   `doc_id` y el registro de uno pisaba al del otro. Un `doc_id` explícito en el sidecar
   `.meta.yaml` sigue mandando (y sustituye a quien lo tuviera, con aviso en el log).
3. **Copias en `data/raw`**: ahora en `data/raw/<zona|inbox>/<doc_id>/<fichero>`. Pueden quedar
   copias huérfanas del formato antiguo en `data/raw/inbox/`; se pueden borrar a mano.
4. **Traces**: `data/logs/trace-YYYYMMDD.jsonl` (una línea por consulta). Para volver al formato
   anterior: `logging.trace_format: json`.
5. **Token de administración**: si defines `SHIPRAG_ADMIN_TOKEN`, la ingesta y el borrado exigen
   `Authorization: Bearer <token>` (la UI muestra un campo para él). Consultar sigue abierto.
   Sin la variable, todo funciona como antes y se avisa en el log y en `doctor`.
6. **API**: tipo no soportado → 415; fichero mayor que `api.max_upload_mb` → 413; zona o tipo
   inválidos → 400 (antes 500). `/api/health` añade `generator`, `documents` y
   `write_protected`; las respuestas añaden `generation_info`.
7. **Overrides locales**: `SHIPRAG_LOCAL_CONFIG=config/local.yaml` aplica un YAML encima del
   perfil (p.ej. LLM en Ollama). Plantilla: `config/local.example.yaml`.
8. **Un solo escritor por índice** (como antes): con `shiprag serve` en marcha, ingerir por la API.
   Una ingesta por CLI sobre el mismo índice no la ve el servidor hasta reiniciarlo.

## Comandos nuevos

```bash
shiprag --profile lite ingest data/sample            # incremental: omite lo que no cambió
shiprag --profile lite ingest data/sample --force    # reindexar todo
shiprag --profile lite ingest /ruta/docs --prune     # además retira lo borrado del origen
shiprag --profile lite ingest /ruta/docs --no-copy   # no duplicar originales en data/raw
shiprag --profile lite documents                     # listar documentos indexados
shiprag --profile lite delete <doc_id> [<doc_id>…]   # eliminar del índice
python scripts/bench_index.py --docs 100 500         # benchmark del índice embebido
```

```bash
# API
curl -X DELETE -H "Authorization: Bearer $SHIPRAG_ADMIN_TOKEN" http://127.0.0.1:8080/api/documents/<doc_id>
curl -H "Authorization: Bearer $SHIPRAG_ADMIN_TOKEN" -F file=@manual.pdf -F force=true http://127.0.0.1:8080/api/ingest
```

## Configuración nueva (`config/default.yaml`)

| Clave | Por defecto | Para qué |
|---|---|---|
| `models.llm.backend` | `llama_cpp` | `openai_compat` → servidor HTTP (vLLM, Ollama, nube) |
| `models.llm.base_url` / `model` / `api_key_env` / `timeout_s` | vacío / vacío / vacío / 60 | Backend HTTP; la key se lee de la variable indicada |
| `models.llm.system_prompt` / `prompt_suffix` | vacío | Prompt propio; `/no_think` para Qwen3 (puesto en `server`) |
| `models.llm.context_max_chars` | 0 (auto) | Presupuesto de evidencia para el LLM |
| `router.emergency_patterns` | lista del buque | `[]` desactiva el override de emergencia |
| `chat.condense_followups` / `use_llm_rewrite` / `history_turns` / `followup_max_terms` | true / true / 3 / 6 | Seguimientos en `/v1/chat/completions` |
| `ingest.copy_to_raw` | true | Copiar originales a `data/raw` |
| `api.admin_token_env` / `max_upload_mb` | `SHIPRAG_ADMIN_TOKEN` / 200 | Protección de escritura y tope de subida |
| `logging.trace_format` / `trace_retention_days` | `jsonl` / 0 | Formato y retención de traces |

---

# 2026-08-10 — Router, verifier NLI, zona DP y tiers de modelos

## Resumen

| Pieza | Antes | Ahora |
|---|---|---|
| Stack `workstation` vs `server` | Misma familia (e5-base) | Tiers distintos: prototipo vs producción |
| Verifier | Solo overlap léxico | Léxico **+** NLI opcional (mDeBERTa) |
| Router | Keywords + boost emergencia | Embeddings (examples) + override duro emergencia |
| Zonas | 8 (sin DP propio) | + `posicionamiento_dinamico` |
| Eval tripulación | Solo golden sintético | Draft paralelo `golden_set_crew_draft.jsonl` |

---

## 1. Dos tiers de modelos (no son intercambiables)

| Tier | Perfil | Uso | Stack |
|---|---|---|---|
| Prototipo / banco de pruebas (~12 GB VRAM) | `workstation` | Iterar en casa | `multilingual-e5-base` + `bge-reranker-base` + Qwen2.5-7B Q4 |
| Producción a bordo | `server` | Operación en el buque | BGE-M3 + `bge-reranker-v2-m3` + Qwen3-32B Q4_K_M |

Si el stack `server` no carga (faltan pesos u OOM), cada componente tiene `fallback_name_or_path` hacia el stack workstation.

Descarga en puerto:

```bash
python scripts/download_models.py --profile workstation   # + NLI
python scripts/download_models.py --profile server        # BGE-M3 + v2-m3 + NLI
# LLM GGUF: copia manual a models/llm/
```

Detalles: [MODELS_WORKSTATION.md](MODELS_WORKSTATION.md) · [PERFILES.md](../PERFILES.md)

---

## 2. Verifier de grounding en dos capas

Config (`models.verifier` en YAML):

```yaml
models:
  verifier:
    backend: lexical   # lite
    # backend: auto    # home / workstation / server → intenta NLI
    name_or_path: models/verifier/mDeBERTa-v3-base-mnli-xnli
    device: cpu
    entailment_threshold: 0.5
    emergency_entailment_threshold: 0.6
```

Flujo:

1. **Capa léxico** (siempre): evidencia, relevancia query↔doc, grounding de tokens.
2. **Capa NLI** (si `backend=auto|nli` y hay pesos): premise = top evidencia; hypothesis = frases de la respuesta; exige *entailment*.
3. Fallo NLI → `ABSTAIN` con citas (no inventa).
4. Lite / pesos ausentes / error de carga → solo léxico (no rompe el pipeline).

Código: `src/shiprag/generation/verifier.py` → `build_verifier()`.

---

## 3. Router por embeddings

### Comportamiento

1. Si hay patrón de emergencia (o `emergency=true`) → **fuerza** zona `emergencias` + modo extractivo (override duro, no solo un boost).
2. Si hay embedder real (no hash) y `router.backend` es `auto`/`embedding` → similitud coseno entre la query y prototipos de zona (media de `examples` en `config/default.yaml`).
3. Si lite / hash / error → keywords como antes.

### Config

```yaml
router:
  backend: auto        # default
  # backend: keywords  # lite fuerza esto
  confidence_threshold: 0.45
  allow_multi_expert: true
  max_experts: 3
```

Cada zona define `keywords` (fallback) y `examples` (5–15 frases para embeddings).

**Nota:** consultas con `blackout` / `distress` / `mayday` van a `emergencias` por seguridad, aunque hablen de DP o GMDSS. Preguntas DP no urgentes (“watch circle”, “thruster allocation”) van a `posicionamiento_dinamico`.

Código: `src/shiprag/experts/router.py` (recibe el mismo embedder que el índice).

---

## 4. Zona `posicionamiento_dinamico`

Buque DP3 (p.ej. DLV2000 / pipe-lay): el DP es un dominio propio, no un subconjunto de puente o maquinaria.

| Campo | Valor |
|---|---|
| Enum | `Zone.POSICIONAMIENTO_DINAMICO` |
| Id YAML | `posicionamiento_dinamico` |
| Keywords típicas | DP, thrusters, watch circle, fallback DP, pérdida de referencia, PRS, DGPS, hydroacoustic, pipe-lay… |
| Ingest | `ZONE_HINTS` + alias de cabecera `# Zona: DP` |

Aún no hay corpus sample DP: las preguntas del draft de eval llevan `must_cite: false` hasta ingerir manuales reales.

---

## 5. Golden set de tripulación (borrador)

| Fichero | Rol |
|---|---|
| `eval/golden_set.jsonl` | Set sintético de CI / smoke (no sustituir a ciegas) |
| `eval/golden_set_crew_draft.jsonl` | Plantilla con preguntas realistas por zona (incl. DP) para revisar con tripulación |

Esquema por línea (JSONL):

```json
{
  "id": "dp-d1",
  "query": "Qué es el watch circle en DP y qué hacer si se supera",
  "expected_zone": "posicionamiento_dinamico",
  "expected_doc_id_contains": null,
  "must_cite": false,
  "emergency": false,
  "should_abstain": false,
  "expected_answer_contains": []
}
```

Cuando tengas preguntas reales:

1. Edita el draft (o copia líneas al golden).
2. Pon `must_cite: true`, `expected_doc_id_contains` y `expected_answer_contains`.
3. Evalúa: `shiprag --profile lite eval eval/golden_set_crew_draft.jsonl`

---

## 6. Diagrama del pipeline actualizado

```
Pregunta
   │
   ▼
¿Patrón / flag emergencia?
   │ sí → emergencias + extractivo
   │ no
   ▼
Router embeddings (examples) ──o── keywords (lite)
   │
   ▼
Retrieve híbrido BM25 + denso → RRF → rerank
   │
   ▼
Generación (extractiva / semi / LLM GGUF opcional)
   │
   ▼
Verifier léxico ──► (opcional) NLI entailment
   │
   ├─ OK → respuesta + citas + trace
   └─ fail → NOT_FOUND / ABSTAIN
```

---

## 7. Checklist rápido tras actualizar

```bash
shiprag profiles
shiprag --profile lite doctor
shiprag --profile lite smoke
pytest -q
shiprag --profile lite eval eval/golden_set.jsonl

# Con modelos (en puerto):
python scripts/download_models.py --profile workstation
shiprag --profile workstation runtime
```

Ver también: [README.md](../README.md) · [PERFILES.md](../PERFILES.md) · [QUICKSTART_CASA.md](../QUICKSTART_CASA.md)
