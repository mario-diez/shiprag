# ShipRAG — Documentación completa del sistema

**Versión del MVP:** 0.1.0  
**Rama:** `cursor/shiprag-offline-mvp-a2a3`  
**Fecha de esta documentación:** 2026-08-10  

Este documento describe **cómo se ha montado** el sistema, **qué hay implementado**, **cómo usarlo**, **qué falta** y **cuáles son los próximos pasos** recomendados. Está pensado tanto para retomar el trabajo en un PC de casa como para una eventual puesta en marcha a bordo.

Documentos complementarios (más cortos):

| Documento | Contenido |
|---|---|
| [README.md](../README.md) | Visión general + quickstart |
| [NOVEDADES.md](NOVEDADES.md) | **Router embeddings, NLI, zona DP, tiers WS/server** |
| [QUICKSTART_CASA.md](../QUICKSTART_CASA.md) | Arranque en 5 minutos en PC |
| [DESCARGA_PC.md](../DESCARGA_PC.md) | Pasar del Cloud Agent al PC |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Decisiones técnicas resumidas |
| [MODELS_WORKSTATION.md](MODELS_WORKSTATION.md) | Stack prototipo vs producción |
| [PERFILES.md](../PERFILES.md) | Cómo lanzar cada perfil |
| [OPENWEBUI.md](OPENWEBUI.md) | Integración Open WebUI |
| [config/default.yaml](../config/default.yaml) | Configuración base (zonas, examples, verifier) |
| [config/profiles/](../config/profiles/) | Perfiles `lite` / `home` / `balanced` / `workstation` / `server` |

---

## 1. Objetivo del sistema

ShipRAG es un **RAG 100% offline** para consultar documentación técnica de un buque:

- Manuales y procedimientos
- Emergencias (MOB, SOPEP, incendio, blackout, GMDSS…)
- Checklists
- Planos/esquemas (con limitaciones de OCR)
- Tablas y metadatos de criticidad

**No es un chatbot conversacional genérico.**  
Prioridad de diseño:

1. **No inventar** cuando no hay evidencia  
2. **Citar** documento / versión / página / sección  
3. **Abstenerse** si la confianza es baja  
4. Ser **especialmente conservador** en emergencias (modo extractivo / solo citas)

---

## 2. Principios de diseño (por qué se montó así)

### 2.1 Abstenerse > sonar fluido

En un contexto naval, un paso inventado en un SOPEP o en un MOB puede ser peligroso. Por eso el MVP:

- Prefiere respuestas **extractivas** (pegar el procedimiento)
- Valida **grounding** (la respuesta debe apoyarse en fragmentos recuperados)
- Devuelve `NO TENGO SUFICIENTE BASE` / `not_found` si no hay evidencia suficiente

### 2.2 Multi-experto por zona

Un único índice global mezcla puentes con máquinas y contamina resultados.  
Se implementaron **mini-expertos por zona** (colección BM25 + densa propia + reglas):

- `puente`, `maquinaria`, `cubierta`, `seguridad`, `emergencias`, `electricidad`, `comunicaciones`, `posicionamiento_dinamico`, `general`

Un **router** clasifica la consulta (similitud de embeddings vs frases `examples` por zona; en `lite`, keywords). Si detecta emergencia, **fuerza** `emergencias` + extractivo. Si la confianza es baja, cae a `general` o combina dominio + fallback.

### 2.3 Recuperación híbrida

Solo embeddings falla con códigos técnicos (`FO-12`, `P-255`, `CH16`).  
Solo BM25 falla con paráfrasis.  
Por eso: **BM25 ⊕ denso → fusión RRF → rerank**.

### 2.4 Offline real

- Sin APIs cloud en runtime
- Modelos opcionales y **locales**
- Perfiles de hardware para no exigir GPU/servidor desde el día 1

### 2.5 Perfiles de hardware

| Perfil | Uso | Modelos |
|---|---|---|
| `lite` (default) | PC casa / demo | Ninguno (hash + lexical + extractivo + keywords) |
| `home` | Casa con CPU | e5-small + MiniLM + NLI si existen |
| `balanced` | GPU 8–12 GB justa | e5-base + bge-reranker (+ NLI), LLM ligero opcional |
| `workstation` | Prototipo / banco de pruebas | e5-base + bge-reranker-base + Qwen2.5-7B |
| `server` | Producción a bordo | BGE-M3 + bge-reranker-v2-m3 + Qwen3-32B (fallback a WS) |

`workstation` y `server` son **tiers distintos** (prototipo vs producción), no una elección arbitraria. Ver [NOVEDADES.md](NOVEDADES.md).

Cada perfil tiene su propio directorio de índices (`data/indexes/<perfil>/`) para no mezclar embeddings distintos.

---

## 3. Arquitectura montada

```
Usuario (UI / CLI / API)
        │
        ▼
┌───────────────────────────┐
│   Orquestador central     │
│  · criticidad / modo      │
│  · router (emb / keywords)│
│  · emergencia → override  │
└─────────────┬─────────────┘
              │
     ┌────────┼────────┐
     ▼        ▼        ▼
  Experto  Experto  Experto …
  (zona)   (zona)   (DP / …)
     │        │        │
     └────────┼────────┘
              ▼
   Recuperación híbrida
   BM25 + densos → RRF
              ▼
        Reranking local
              ▼
   Generación controlada
   (extractiva / semi / LLM*)
              ▼
   Verificador grounding
   (léxico ± NLI) + abstención
              ▼
   Respuesta + citas + trace
```

`*` El LLM local es opcional. En emergencias manda el modo extractivo.

### 3.1 Flujo de ingesta

1. Entrada: PDF / TXT / MD / imagen  
2. Extracción: PyMuPDF (texto) + pdfplumber (tablas)  
3. OCR opcional (Tesseract) si la página tiene poco texto  
4. Detección de estructura: headings, listas, avisos (`PELIGRO`, `WARNING`…)  
5. Chunking **estructural** (por sección / procedimiento), no solo ventana fija  
6. Metadatos: `doc_id`, versión, zona, tipo, idioma, criticidad, páginas, sección  
7. Sidecar opcional `*.meta.yaml` para forzar metadatos  
8. Indexado dual por zona: BM25 + Chroma (o fallback denso)

### 3.2 Flujo de consulta

1. Router → zona(s) + flag emergencia + modo sugerido  
2. Retrieve híbrido con filtros  
3. Rerank (cross-encoder o lexical)  
4. Generación según modo  
5. Verificación: evidencia + relevancia query↔doc + grounding  
6. Persistencia de trace en `data/logs/trace_*.json`

---

## 4. Estructura del repositorio

```
shiprag/
├── README.md                 # Visión + quickstart
├── QUICKSTART_CASA.md        # Arranque PC casa
├── DESCARGA_PC.md            # Cómo sacar el código del Cloud Agent
├── docs/
│   ├── ARCHITECTURE.md       # Decisiones cortas
│   └── DOCUMENTACION.md      # Este documento
├── config/
│   ├── default.yaml          # Base común
│   └── profiles/
│       ├── lite.yaml
│       ├── home.yaml
│       ├── balanced.yaml
│       ├── workstation.yaml
│       └── server.yaml
├── src/shiprag/
│   ├── cli.py                # CLI: ingest/query/serve/eval/smoke/doctor…
│   ├── doctor.py             # Diagnóstico de entorno
│   ├── eval_runner.py        # Evaluación golden set
│   ├── core/                 # Config, schemas, logging
│   ├── ingest/               # PDF/OCR/chunking/pipeline
│   ├── index/                # Embeddings, BM25, Chroma/híbrido
│   ├── retrieval/            # Híbrido RRF + rerank
│   ├── experts/              # Router + ZoneExpert
│   ├── generation/           # Extractivo, LLM opcional, verifier
│   ├── orchestration/        # Pipeline end-to-end
│   ├── api/                  # FastAPI
│   └── ui/static/            # UI local
├── data/sample/              # Corpus sintético de demo
├── eval/
│   ├── golden_set.jsonl           # Banco sintético CI
│   └── golden_set_crew_draft.jsonl  # Plantilla preguntas tripulación
├── tests/                    # Tests unitarios
├── scripts/                  # Arranque, pack, PDFs, descarga modelos
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── pyproject.toml
```

---

## 5. Módulos implementados (detalle)

### 5.1 Core — `src/shiprag/core/`

| Fichero | Responsabilidad |
|---|---|
| `config.py` | Carga YAML, merge de perfiles, `AppConfig` tipado, `SHIPRAG_PROFILE` / `SHIPRAG_CONFIG` |
| `schemas.py` | Modelos Pydantic: Chunk, Query, Citation, Trace, zonas, criticidad, modos |
| `logging.py` | Logs locales + escritura de traces de auditoría |

### 5.2 Ingesta — `src/shiprag/ingest/`

| Fichero | Responsabilidad |
|---|---|
| `pdf_extractor.py` | Extracción PDF/imagen/texto + headings + tablas |
| `ocr.py` | Wrapper Tesseract local (opcional) |
| `chunker.py` | Chunking estructural con overlap controlado |
| `pipeline.py` | Ingesta end-to-end, inferencia de zona/tipo, sidecars `.meta.yaml` |

**Sidecar de metadatos (ejemplo):**

```yaml
doc_id: safe-fire-01
title: Procedimiento incendio en sala de máquinas
version: "1.3"
zone: emergencias
doc_type: emergency
language: es
criticality: critical
tags: [incendio, CO2]
```

Colocar como `mi_documento.meta.yaml` junto al fichero.

### 5.3 Índice — `src/shiprag/index/`

| Fichero | Responsabilidad |
|---|---|
| `embeddings.py` | `HashEmbedding` (lite) o `SentenceTransformer` (home/WS/server); `fallback_name_or_path` |
| `lexical.py` | BM25 persistente por zona (`lexical.jsonl`) |
| `store.py` | `DenseStore` (Chroma o fallback) + `HybridIndex` multi-zona |

Los chunks de cada zona se espejan también en `general` para consultas globales, pero el orquestador **evita consultar `general`** si ya hay expertos de dominio (para no contaminar).

### 5.4 Retrieval — `src/shiprag/retrieval/`

| Fichero | Responsabilidad |
|---|---|
| `hybrid.py` | BM25 + denso + Reciprocal Rank Fusion |
| `reranker.py` | Cross-encoder o lexical; bonus por códigos `FO-12` y overlap de título |

### 5.5 Expertos — `src/shiprag/experts/`

| Fichero | Responsabilidad |
|---|---|
| `router.py` | Embeddings (examples) o keywords; override duro de emergencia → `emergencias` |
| `expert.py` | Perfil por zona (modo por defecto, bias de criticidad) |

Patrones de emergencia detectados (entre otros): hombre al agua, SOPEP, mayday, distress, blackout, incendio en…, abandono, inundación.  
Zona DP: consultas no urgentes de thrusters / watch circle / PRS → `posicionamiento_dinamico`.

### 5.6 Generación y verificación — `src/shiprag/generation/`

| Fichero | Responsabilidad |
|---|---|
| `generator.py` | Extractivo / semi / citations_only; LLM GGUF opcional (+ `fallback_name_or_path`) |
| `verifier.py` | Léxico (evidencia, qrel, grounding) + NLI opcional (`build_verifier`) |

**Verifier NLI** (`models.verifier`): si `backend=auto|nli` y hay pesos mDeBERTa, la respuesta solo se acepta si pasa el umbral léxico **y** entailment evidencia→afirmación. En `lite` o si falla la carga → solo léxico.

**Modos de respuesta:**

| Modo | Comportamiento |
|---|---|
| `extractive` | Pasos/avisos citando el documento (preferido en emergencia) |
| `citations_only` | Solo extractos literales numerados |
| `semi` | Extractivo + evidencia adicional breve |
| `generative` | Solo si hay LLM local y pasa el verifier |
| `auto` | El router decide (emergencia → extractivo) |

### 5.7 Orquestación — `src/shiprag/orchestration/pipeline.py`

Une router → retrieve → rerank → generate → verify → trace.  
En emergencia, **bloquea** `generative` y fuerza extractivo.

### 5.8 API + UI — `src/shiprag/api/` + `ui/static/`

- FastAPI en `127.0.0.1:8080` (configurable)
- Endpoints: `/api/health`, `/api/profiles`, `/api/zones`, `/api/query`, `/api/ingest`, `/api/documents`
- **API OpenAI-compatible** para Open WebUI: `/v1/models`, `/v1/chat/completions` (ver [OPENWEBUI.md](OPENWEBUI.md))
- UI: consulta, chips de ejemplo, botón **EMERGENCIA · solo citas**, ingesta, citas, trace

### 5.9 CLI — `shiprag`

```text
shiprag --profile lite|home|server <comando>

ingest <path> [--zone]
query "..." [--emergency] [--mode ...]
serve [--host] [--port]
eval eval/golden_set.jsonl
smoke [--skip-eval]
doctor
profiles
runtime
```

### 5.10 Doctor, smoke y evaluación

- **`doctor`:** comprueba Python, dependencias, perfil, sample, disco, índice, OCR  
- **`smoke`:** 3 checks rápidos (MOB, FO-12, abstención) + eval opcional  
- **`eval`:** métricas sobre golden set:
  - precisión de routing por zona
  - hit rate del documento esperado
  - tasa de citas
  - contención de respuesta (términos esperados)
  - abstención correcta

**Estado actual del golden set (corpus sample, perfil lite):** métricas al **100%** en la última corrida de desarrollo.

---

## 6. Corpus de demostración incluido

En `data/sample/` (TXT + PDF generados + sidecars):

| Documento | Zona | Tipo |
|---|---|---|
| emergencias_hombre_al_agua | emergencias | emergency |
| emergencias_sopep | emergencias | emergency |
| seguridad_incendio_maquinas | emergencias | emergency |
| electricidad_blackout | electricidad | procedure |
| comunicaciones_gmdss_distress | comunicaciones | emergency |
| maquinaria_generador | maquinaria | manual |
| puente_checklist_guardia | puente | checklist |
| cubierta_amarre | cubierta | checklist |

> Son documentos **sintéticos de demo**, no manuales reales de un buque. Sirven para validar el pipeline.

---

## 7. Cómo está configurado

### 7.1 Selección de perfil

```bash
shiprag --profile lite serve
# o
export SHIPRAG_PROFILE=lite
shiprag serve
```

Merge: `config/default.yaml` ← `config/profiles/<perfil>.yaml`.

### 7.2 Backends de modelos

En cada perfil:

```yaml
models:
  embedding:
    backend: hash | auto | sentence_transformers
  reranker:
    backend: lexical | auto | cross_encoder
  llm:
    enabled: false   # true solo con GGUF local
```

- `lite` **fuerza** `hash` + `lexical` (no intenta cargar transformers).  
- `home`/`server` usan modelos si existen en `models/`; si no, degradan sin romper.

### 7.3 Descarga de pesos (solo en puerto / con red)

```bash
python scripts/download_models.py --profile home
python scripts/download_models.py --profile server
```

---

## 8. Cómo arrancar (resumen operativo)

### 8.1 PC de casa (recomendado primero)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

shiprag --profile lite doctor
shiprag --profile lite ingest data/sample
shiprag --profile lite smoke
shiprag --profile lite serve --port 8080
# http://127.0.0.1:8080
```

Atajos:

- Linux/macOS: `bash scripts/start_lite.sh`
- Windows: `scripts\start_lite.bat`
- Docker: `docker compose up --build`

### 8.2 Empaquetar para copiar

```bash
python scripts/pack_for_home.py
# → dist/shiprag-home-kit-YYYYMMDD.zip
```

### 8.3 Preguntas de prueba sugeridas

| Pregunta | Esperado |
|---|---|
| procedimiento hombre al agua | citas MOB |
| alarma FO-12 del generador | FO-12 / combustible |
| derrame SOPEP | contención + notificación |
| blackout eléctrico | generador emergencia |
| incendio en sala de máquinas | procedimiento fuego |
| amarre / snap-back | checklist cubierta |
| receta de paella | abstención |

---

## 9. Qué está montado (checklist de entrega MVP)

### Funcional

- [x] Ingesta offline PDF/TXT/MD/imagen  
- [x] OCR local opcional  
- [x] Extracción de tablas (pdfplumber)  
- [x] Chunking estructural  
- [x] Metadatos + sidecars YAML  
- [x] Índice híbrido BM25 + vectorial por zona  
- [x] Reranking local (CE o lexical)  
- [x] Router multi-experto por zona (embeddings + keywords; override emergencia)  
- [x] Zona `posicionamiento_dinamico` (DP)  
- [x] Consulta global / multi-experto  
- [x] Modos extractivo / semi / solo citas / auto  
- [x] Modo emergencia conservador  
- [x] Citas con documento/versión/página/sección  
- [x] Verificación anti-alucinación (léxico ± NLI) + abstención  
- [x] Detección heurística de conflictos entre procedimientos  
- [x] Trace de recuperación auditable  
- [x] API local + UI  
- [x] CLI completa  
- [x] Perfiles lite / home / balanced / workstation / server (tiers WS≠server)  
- [x] Fallback de modelos (`fallback_name_or_path`) en server  
- [x] Doctor + smoke + eval  
- [x] Docker lite  
- [x] Kit zip para PC casa  
- [x] Tests unitarios + golden set + draft tripulación  

### No montado / parcial (ver sección 11)

- [ ] Corpus real del buque (incl. manuales DP)  
- [ ] Modelos neuronales empaquetados en el repo  
- [ ] LLM local por defecto  
- [ ] VLM para planos/P&ID  
- [ ] Repo GitHub remoto enlazado a este agente  
- [ ] Auth / multi-usuario / RBAC  
- [ ] Firma/integridad criptográfica de manuales  
- [ ] Ajuste fino de `examples` del router con logs reales  
- [ ] HITL de validación de citas  
- [ ] Integración bitácora ISM / export oficial  
- [ ] Fusionar draft de tripulación en golden oficial  

---

## 10. Decisiones importantes (y alternativas descartadas)

| Decisión | Alternativa rechazada | Motivo |
|---|---|---|
| Extractivo-first en emergencias | Solo LLM generativo | Riesgo de pasos inventados |
| Híbrido BM25+denso+RRF | Solo vectores | Códigos técnicos (`FO-12`) |
| Chroma embebido + BM25 fichero | Elastic/Qdrant server | Menos daemons a bordo |
| Tiers workstation ≠ server | Un único stack “server” | Prototipo en casa vs producción a bordo |
| Verifier léxico + NLI opcional | Confiar en el LLM | Doble anclaje sin romper lite |
| Router embeddings + override emergencia | Solo keywords / solo clasificador | Offline, reusa embedder, seguridad en crisis |
| Zona DP propia | Diluir DP en puente/máquina | DP3 es dominio crítico |
| LLM opcional y off en lite | LLM obligatorio | Offline + coste + control |
| Abstención explícita | “Mejor respuesta posible” | Seguridad operativa |
| Índices por zona | Un solo índice | Menos contaminación cruzada |
| Traces en disco | Telemetría cloud | Auditoría air-gapped |

---

## 11. Qué faltaría (gap analysis honesto)

### 11.1 Crítico para uso real a bordo

1. **Corpus real** versionado (manuales ISM, SOPEP, planos, checklists del buque concreto).  
2. **Modelos descargados en puerto** (`home` o `server`) y validados offline.  
3. **Proceso de gobernanza documental:** qué versión es vigente, quién aprueba, caducidad.  
4. **Banco de evaluación con preguntas reales de la tripulación** (no solo el sample).  
5. **Prueba en hardware objetivo** (PC camarote / server del buque / NAS).

### 11.2 Calidad de recuperación / generación

1. Afinar `examples` del router con logs reales de tripulación (ya no es solo keywords).  
2. Corpus e índice para `posicionamiento_dinamico` (manuales DP del buque).  
3. Mejor manejo de tablas multi-página y anexos.  
4. Mejor OCR de planos densos; idealmente capas CAD/texto.  
5. Conflictos entre versiones: hoy es heurístico; falta política “versión vigente gana”.  
6. Fusionar `golden_set_crew_draft.jsonl` tras revisión humana.

### 11.3 Seguridad y operación

1. Modo kiosko / pantalla puente con UX de emergencia aún más simple.  
2. Control de acceso (oficiales vs tripulación).  
3. Hash/firma de paquetes documentales.  
4. Backup/restore de índices.  
5. Procedimiento escrito de “qué hacer si ShipRAG no responde / abstiene”.

### 11.4 Producto / despliegue

1. Enlazar **repositorio Git remoto** (ahora el Cloud Agent no tiene repo).  
2. Paquete air-gap (tar.gz con código + modelos + corpus).  
3. Instalador Windows más guiado.  
4. Monitorización local (disco, latencia, tamaño índice).  
5. Export de traces a formato ISM/auditoría.

### 11.5 Limitaciones técnicas conocidas del MVP

- Sin pesos neuronales, la calidad semántica es limitada (aunque BM25 + extractivo ya demuestran el flujo).  
- Los PDFs sample son conversiones simples de TXT; no representan planos reales.  
- El LLM generativo no está activado; y **no debería ser el modo por defecto en emergencias**.  
- La UI es funcional, no un producto de puente certificado.  
- No hay garantía regulatoria ni certificación de software de seguridad: es una base técnica.

---

## 12. Próximos pasos recomendados (ordenados)

### Fase A — Validación en PC de casa (ahora)

1. Descargar el proyecto desde el agente (`DESCARGA_PC.md`).  
2. Arrancar con `--profile lite`.  
3. Ejecutar `doctor` + `smoke` + UI.  
4. Probar las preguntas de la tabla (incluir una absurda para ver abstención).  
5. Subir 2–3 PDFs reales (aunque sean extractos) y ver citas/páginas.

**Criterio de éxito:** el flujo se entiende, abstiene cuando toca, cita fuentes, no requiere GPU.

### Fase B — Calidad en casa (`home`)

1. `python scripts/download_models.py --profile home`  
2. Re-ingerir corpus en perfil `home`.  
3. Comparar `lite` vs `home` en las mismas preguntas.  
4. Ampliar golden set con 20–30 preguntas propias.

**Criterio de éxito:** mejora clara en paráfrasis y ranking sin romper abstención.

### Fase C — Corpus del buque

1. Inventariar documentos oficiales y versiones.  
2. Etiquetar zona / tipo / criticidad (sidecars).  
3. Ingestar por lotes y medir eval.  
4. Definir “fuente de verdad” ante conflictos.

### Fase D — Despliegue operativo (`server`)

1. Hardware objetivo + perfil `server`.  
2. (Opcional) GGUF solo para modo semi/no crítico.  
3. UI de emergencia en pantalla dedicada.  
4. Procedimiento de actualización documental en puerto.  
5. Entrenamiento corto a la tripulación: cuándo confiar / cuándo abstenerse.

### Fase E — Madurez

1. HITL de citas.  
2. Ajuste continuo de `examples` del router + corpus DP.  
3. Integridad de paquetes.  
4. Export ISM.  
5. Evaluación continua (regresión en cada actualización de corpus; fusionar draft tripulación).

---

## 13. Métricas y calidad (cómo medir)

El runner `shiprag eval eval/golden_set.jsonl` reporta:

| Métrica | Significado |
|---|---|
| `zone_routing_accuracy` | ¿Se eligió la zona esperada? |
| `retrieval_doc_hit_rate` | ¿Aparece el documento correcto entre recuperados/citas? |
| `citation_rate_on_answerable` | ¿Hay citas cuando debería responder? |
| `answer_contains_rate` | ¿La respuesta/citas contienen términos clave esperados? |
| `correct_abstention_rate` | ¿Se abstiene en preguntas fuera de corpus? |

**Interpretación recomendada para producción:**

- Abstención correcta alta (>0.9) es más importante que “responder siempre”.  
- En emergencias, priorizar citas literales sobre fluidez.  
- Cualquier regresión en abstención o citas = bloqueo de despliegue.

---

## 14. Operación air-gap (checklist pre-zarpe)

Antes de quedarse sin internet:

1. [ ] Código + config en el servidor del buque  
2. [ ] Pesos en `models/` según perfil elegido  
3. [ ] Corpus oficial ingerido y versionado  
4. [ ] `shiprag doctor` en verde  
5. [ ] `shiprag eval` sobre golden set de la flota en verde  
6. [ ] `shiprag smoke` en verde  
7. [ ] Backup de `data/indexes/` y `data/raw/`  
8. [ ] Procedimiento manual en papel sigue siendo la referencia legal/operativa  

> ShipRAG asiste; **no sustituye** el manual oficial sellado ni la decisión del Capitán.

---

## 15. Troubleshooting rápido

| Síntoma | Qué mirar |
|---|---|
| “API no disponible” | ¿`shiprag serve` está arriba? ¿puerto 8080 libre? |
| Respuestas pobres en `lite` | Normal sin embeddings; probar `home` con pesos |
| No encuentra un PDF escaneado | Activar OCR + instalar Tesseract; revisar texto extraído |
| Contamina otras zonas | ¿Se forzó zona en sidecar? ¿se está usando índice de otro perfil? |
| Quiere “inventar” en emergencia | Usar botón EMERGENCIA / `citations_only` |
| `doctor` falla deps | `pip install -e ".[dev]"` dentro del venv |
| Índices raros tras cambiar perfil | Cada perfil tiene carpeta propia; re-ingerir |

Logs y traces: `data/logs/shiprag.log`, `data/logs/trace_*.json`.

---

## 16. Historial de construcción (cómo se montó)

Trabajo realizado en el Cloud Agent, en la rama `cursor/shiprag-offline-mvp-a2a3`, sin repositorio GitHub remoto enlazado:

1. **Arquitectura + MVP core:** ingesta, híbrido, router, extractivo, verifier, API/UI, sample, tests/eval.  
2. **Perfiles de hardware:** `lite` / `home` / `server` configurables por CLI/env.  
3. **Kit PC casa:** quickstart, scripts Windows/Linux, smoke, PDFs sample, UI emergencia, pack zip.  
4. **Doctor + Docker + más corpus + guía de descarga.**  
5. **Esta documentación extensa.**

Commits principales (locales al agente):

- `feat: MVP ShipRAG offline multi-experto…`  
- `feat: perfiles lite/home/server…`  
- `feat: kit PC casa…`  
- `feat: doctor, Docker lite, más corpus…`  

> **Acción pendiente de usuario:** enlazar un repo remoto o descargar el zip para no perder el trabajo si la sesión cloud expira.

---

## 17. Conclusión

Hay un **MVP serio y modular**: offline, multi-experto, anti-alucinación, auditable, usable en PC de casa con perfil `lite`, y preparado para escalar a modelos locales cuando haya hardware.

Lo que falta para “usar en un barco de verdad” no es tanto más framework, sino:

1. **Corpus oficial**  
2. **Validación humana con preguntas reales**  
3. **Modelos locales empaquetados**  
4. **Gobernanza de versiones**  
5. **Despliegue en el hardware objetivo**

Hasta completar eso, ShipRAG debe tratarse como **asistente documental con abstención**, nunca como autoridad operativa por encima del manual del buque.
