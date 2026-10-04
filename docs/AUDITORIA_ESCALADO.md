# Auditoría: ¿sirve ShipRAG para la documentación de una empresa?

**Fecha:** 2026-10-04 · **Base auditada:** `main` @ `23e4260` · **Correcciones:** rama `feat/fundamentos-escalado`
**Relacionado:** [PLAN_EMPRESA.md](PLAN_EMPRESA.md) (qué hacer y en qué orden) · [LLM.md](LLM.md) (modelos e inferencia) ·
[NOVEDADES.md](NOVEDADES.md) (cambios aplicados y notas de migración)

---

## 0. Resumen

- **El enfoque es el correcto** para “acceder de forma sencilla a mucha documentación”: búsqueda
  híbrida (palabras + significado), reordenación, respuestas con cita (documento, versión, página),
  verificación y **abstención** cuando no hay evidencia. Es justo lo que se pide a un asistente
  documental serio.
- **La implementación de `main` no aguantaba crecer**: al reingerir un documento modificado
  quedaban sus versiones viejas respondiendo; la ingesta se ralentizaba de forma cuadrática; el
  reranker se cargaba una vez por zona; la subida por API permitía escribir fuera de su carpeta; y
  el LLM, con un prompt crudo, devolvía respuestas basura que el verificador **aceptaba**.
- **La rama `feat/fundamentos-escalado` corrige todo eso (Fase 1)** sin cambiar la arquitectura y
  con la evaluación intacta (mismos resultados caso a caso en `lite`; mismas métricas en
  `balanced`, que además tarda entre 2,4 y 4,4 veces menos).
- **Lo que falta para una empresa es estructural** (Fases 2–3 del plan): un motor de búsqueda
  dedicado en lugar de ficheros en memoria, **permisos por usuario**, conectores con
  SharePoint/Drive/Confluence, formatos Office y una taxonomía que no sea la del buque.
- **Veredicto:** sí tiene sentido usarlo como base, **no tal cual**. Con la Fase 1 es apto para una
  prueba de concepto con un departamento (hasta ~10⁴–10⁵ fragmentos, pocos usuarios, sin
  documentos restringidos). Para producción en toda la empresa hacen falta las Fases 2 y 3.

---

## 1. Contexto: para qué se diseñó y para qué se quiere ahora

| | Diseño original (buque) | Uso nuevo (empresa) |
|---|---|---|
| Documentos | Decenas–cientos de manuales y procedimientos | Miles–cientos de miles, en varios repositorios |
| Usuarios | Tripulación, uno o pocos a la vez | Decenas a la vez, con permisos distintos |
| Conectividad | **Sin internet** (air-gap) | Red corporativa; la nube puede estar permitida o no |
| Riesgo dominante | Un paso inventado en una emergencia | Fuga de información entre áreas; respuestas desactualizadas |
| Taxonomía | Zonas del buque (puente, máquinas, DP…) | Áreas/departamentos/tipos de documento propios |

Las decisiones de seguridad del diseño (extractivo en emergencias, abstención, trazas) **siguen
siendo valiosas**. Lo que no encaja es el almacenamiento, la falta de identidad y lo cableado del
dominio.

---

## 2. Lo que hay (inventario técnico)

### 2.1 Flujo de ingesta

```
fichero (PDF/TXT/MD/imagen) ──▶ extracción (PyMuPDF + pdfplumber; OCR Tesseract opcional)
   ──▶ bloques con estructura (títulos, listas, avisos, tablas)
   ──▶ chunking estructural (sección/procedimiento; máx. 1.200 chars, solape 150)
   ──▶ metadatos (zona, tipo, criticidad, versión, idioma; sidecar *.meta.yaml opcional)
   ──▶ [Fase 1] registro + huella: ¿mismo contenido? → omitir · ¿versión nueva? → sustituir
   ──▶ índice por zona: BM25 (rank_bm25, JSONL) + vectores (Chroma) + copia en "general"
```

Código: `src/shiprag/ingest/` (`pipeline.py`, `pdf_extractor.py`, `chunker.py`, `ocr.py`,
`registry.py`) e `src/shiprag/index/` (`lexical.py`, `store.py`, `embeddings.py`).

### 2.2 Flujo de consulta

```
pregunta ──▶ router (patrones de emergencia → zona emergencias; si no, embeddings/keywords → zona(s))
   ──▶ por zona: BM25 top-40 + vectores top-40 ──▶ fusión RRF ──▶ rerank (cross-encoder o léxico)
   ──▶ generación: extractiva / semi / solo citas / LLM opcional
   ──▶ verificador: evidencia + relevancia + grounding léxico (± NLI) ──▶ respuesta o abstención
   ──▶ trace JSON (por qué se eligió cada fragmento)
```

Código: `src/shiprag/experts/`, `retrieval/`, `generation/`, `orchestration/pipeline.py`.

### 2.3 Almacenamiento en disco

```
data/
  raw/<zona|inbox>/<doc_id>/<fichero>    copias de originales  (main: raw/<zona|inbox>/<fichero>)
  raw/uploads/<fichero>                  subidas por API
  indexes/<perfil>/
    <zona>/bm25/lexical.jsonl            chunks; el BM25 se reconstruye EN MEMORIA al cargar
    <zona>/chroma/                       vectores (SQLite + HNSW)
    general/…                            copia de TODOS los chunks (consultas globales)
    documents/<doc_id>.json              registro de documentos (huella, origen, nº de chunks)
    manifest.json                        resultado de la última ingesta de directorio
  logs/shiprag.log · trace-YYYYMMDD.jsonl (main: un trace_*.json por consulta) · last_eval.json
```

### 2.4 Modelos y perfiles

| Perfil | Embeddings | Reranker | LLM | Verificador |
|---|---|---|---|---|
| `lite` (defecto) | hash (sin modelo) | léxico | no | léxico |
| `home` | e5-small (CPU) | MiniLM | no | léxico + NLI si hay pesos |
| `balanced` | e5-base (GPU) | bge-reranker-base (GPU) | Qwen2.5-1.5B GGUF | léxico + NLI si hay pesos |
| `workstation` | e5-base | bge-reranker-base | Qwen2.5-7B GGUF | léxico + NLI |
| `server` | BGE-M3 | bge-reranker-v2-m3 | Qwen3-32B GGUF (fallback 7B) | léxico + NLI |

### 2.5 Interfaces

| Interfaz | Detalle |
|---|---|
| CLI `shiprag` | `ingest [--force --prune --no-copy]`, `documents`, `delete`, `query`, `serve`, `eval`, `smoke`, `doctor`, `profiles`, `runtime` |
| API REST | `GET /api/health`, `/api/profiles`, `/api/zones`, `/api/documents` · `POST /api/query` · `POST /api/ingest` y `DELETE /api/documents/{doc_id}` (con token si `SHIPRAG_ADMIN_TOKEN`) |
| API compatible OpenAI | `GET /v1/models`, `POST /v1/chat/completions` → Open WebUI |
| UI web | Consulta, botón de emergencia (solo citas), ingesta, citas y trace |
| Docker | `docker compose up` (perfil `lite`) y `--profile openwebui` |

### 2.6 Calidad y pruebas

- Tests: 13 en `main` → 57 en la rama (índice, API, LLM con backends simulados, chat, configuración).
- Evaluación: `eval/golden_set.jsonl` (12 preguntas sintéticas) y
  `eval/golden_set_crew_draft.jsonl` (47 preguntas borrador **sin etiquetar**: sus métricas de
  recuperación y citas salen a 0 porque no tienen `expected_doc_id_contains` ni `must_cite`).
- Corpus de ejemplo: 8 documentos sintéticos del buque, cada uno en `.txt` y en `.pdf`.

### 2.7 Entorno de la máquina auditada

RTX 4070 SUPER (12 GB), PyTorch 2.6 con CUDA operativo, Chroma 1.5.9, llama-cpp-python 0.3.34
**compilado sin GPU**, modelos presentes: e5-small, e5-base, bge-reranker-base, MiniLM,
Qwen2.5-1.5B GGUF. **Falta el verificador NLI** (`models/verifier/`), así que `home` y `balanced`
verifican solo léxicamente. Dependencias con versiones nuevas disponibles (p.ej. torch 2.14,
sentence-transformers 6.1); no se han actualizado en esta rama porque exige revalidar.

---

## 3. Hallazgos

Severidad pensando en el uso en empresa. Estado: **Corregido** (en la rama) · **Mitigado**
(mejora parcial) · **Pendiente Fn** (fase del [plan](PLAN_EMPRESA.md)).

| # | Sev. | Hallazgo | Evidencia en `main` | Estado |
|---|---|---|---|---|
| H1 | Crítica | Reingerir un documento modificado **dejaba los fragmentos viejos** en el índice: respuestas mezclando versiones | El `chunk_id` incluye un hash del texto y la ingesta solo hacía upsert; `LexicalStore.delete_doc` nunca se llamaba y Chroma no tenía borrado | **Corregido**: `replace_document` + registro |
| H2 | Crítica | La subida por API escribía donde dijera el nombre del fichero del cliente (`../../x`) | `api/app.py`: `raw_dir / file.filename` | **Corregido**: nombre saneado + test |
| H3 | Crítica | El LLM (prompt crudo) generaba bucles y `[n]`; el verificador **aceptó** un bucle como `ok` | Ver [LLM.md §2](LLM.md#2-qué-había-y-qué-fallaba-medido) | **Corregido** |
| H4 | Crítica (empresa) | **Sin permisos por documento ni identidad**: cualquiera ve todo | No existe el concepto de usuario | **Pendiente F3** |
| H5 | Alta | Ingesta **cuadrática**: cada fichero reconstruía y reescribía todo el BM25 | `LexicalStore.upsert → _rebuild()` por fichero; ver §4.1 | **Corregido** en lotes (directorio) · **Mitigado** en subidas sueltas (O(N) por subida) |
| H6 | Alta | Mismo nombre de fichero en dos carpetas: `doc_id` igual y misma copia en `data/raw/inbox/` → uno pisaba al otro | `doc_id = slug(stem)` | **Corregido** (identidad por origen; sufijo en colisión) |
| H7 | Alta | Sin borrado ni sincronización con el origen: lo borrado seguía respondiendo | No había comando ni endpoint | **Corregido**: `delete`, `DELETE /api/documents`, `--prune` |
| H8 | Alta | Reranker cargado **una vez por zona** (hasta 9 copias en GPU) y modelos recargados por cada `Orchestrator` | 16 cargas del reranker y 4 del embedder en una evaluación; 3–6 s en la primera consulta de cada zona | **Corregido**: `core/model_cache.py` (1 carga) |
| H9 | Alta | El chat (`async`) y la ingesta bloqueaban el event loop: una consulta lenta paraba a todos | `async def` que llamaban a código síncrono pesado | **Corregido** (hilos) |
| H10 | Alta | `llama_cpp.Llama` (no thread-safe) usado desde el pool de hilos sin bloqueo | `/api/query` ya era síncrono → hilos concurrentes | **Corregido** (lock) · **Pendiente F2/F4**: servidor de inferencia |
| H11 | Alta | Escritura (ingesta) sin autenticación; en Docker escucha en `0.0.0.0` | `docker-compose.yml` | **Mitigado**: token de administración · **Pendiente F3**: SSO |
| H12 | Alta (empresa) | Dominio del buque cableado: `Zone` enum, `ZONE_HINTS`, patrones de emergencia (“plan de emergencia de la oficina” → modo solo citas) | `core/schemas.py`, `ingest/pipeline.py`, `experts/router.py` | **Mitigado**: patrones configurables · **Pendiente F2**: taxonomía configurable |
| H13 | Alta (empresa) | Formatos: solo PDF/TXT/MD/imagen. Sin Word, Excel, PowerPoint, HTML ni correo | `pdf_extractor.py` | **Pendiente F2** |
| H14 | Alta (empresa) | Sin conectores (SharePoint, Drive, Confluence) ni cambios incrementales desde el origen | — | **Pendiente F3** |
| H15 | Alta (escala) | Almacenamiento embebido: BM25 entero en RAM por proceso, consulta O(N) en Python, un solo escritor, sin réplicas | `rank_bm25`, ver §4.1 | **Pendiente F2** (motor dedicado) |
| H16 | Media | Cada chunk se convertía en vector **dos veces** (zona + copia general) | `HybridIndex.upsert_chunks` | **Corregido** |
| H17 | Media | La copia “general” duplica almacenamiento e inserciones | Diseño por zonas | **Pendiente F2** (una colección + filtros) |
| H18 | Media | BM25 sin stemming ni stopwords en español (“procedimientos” ≠ “procedimiento”) | `TOKEN_RE` | **Pendiente F2** |
| H19 | Media | Chat sin contexto: “¿y si no arranca?” se buscaba tal cual | Solo el último mensaje | **Corregido** (reescritura de seguimientos) |
| H20 | Media | Lotes de más de ~5.000 chunks a Chroma fallaban (PDF muy grande) | Un único `upsert` | **Corregido** (lotes de 1.000) |
| H21 | Media | Escritura no atómica de índice y registro: un corte a mitad dejaba ficheros corruptos | `open("w")` directo | **Corregido** (temporal + `os.replace`) |
| H22 | Media | Traces: un fichero por consulta y sin retención; contienen preguntas (posibles datos personales) | `core/logging.py` | **Corregido** (JSONL diario + retención) · **Pendiente F3**: política RGPD |
| H23 | Media | Subida sin límite de tamaño, leída entera en memoria | `await file.read()` | **Corregido** (`max_upload_mb`, por bloques) |
| H24 | Media (entorno) | `llama-cpp-python` sin CUDA: el LLM corre en CPU aunque el perfil pida GPU | `llama_supports_gpu_offload() == False`; las guías (`PERFILES.md`, `MODELS_WORKSTATION.md`) proponían el wheel `…/whl/cpu` | **Mitigado**: `doctor` lo avisa y las guías lo advierten; ver [LLM.md §6.1](LLM.md#61-llamacpp-en-proceso-con-gpu) |
| H25 | Media | Respuesta del LLM sin contenido (solo `[1]`) daba grounding 1,0 y estado `ok` | Visto con Qwen2.5-1.5B | **Corregido** (→ semi-extractivo) |
| H26 | Baja | Zona o tipo inválidos en `/api/ingest` devolvían 500 | `Zone(zone)` sin capturar | **Corregido** (400) |
| H27 | Baja | Verificador NLI no descargado en esta máquina | `models/verifier/` ausente | Acción: `python scripts/download_models.py --profile balanced` |
| H28 | Baja | Citas duplicadas: el corpus de ejemplo tiene cada documento en `.txt` y `.pdf` | Ver §4.3 | **Pendiente F2** (casi-duplicados) |
| H29 | Baja | Golden set de tripulación sin etiquetar: no mide recuperación ni citas | `eval/golden_set_crew_draft.jsonl` | **Pendiente F4** |

---

## 4. Mediciones (antes → después)

### 4.1 Índice embebido: ingesta y consulta

`python scripts/bench_index.py` con documentos sintéticos de 20 fragmentos (~1.200 caracteres),
embeddings *hash* (para medir solo el almacenamiento, sin GPU), repartidos en 4 zonas. El mismo
script se ejecutó sobre `main` y sobre la rama.

| Documentos (fragmentos) | `main` · por fichero | Rama · por fichero (API) | Rama · por lote (`ingest <dir>`) | ms/doc `main` → lote | BM25 p50 | Vectorial p50 |
|---|---|---|---|---|---|---|
| 50 (1.000) | 15,3 s | 10,6 s | 6,6 s | 306 → 131 | 1,1 ms | 3,3 ms |
| 100 (2.000) | 28,9 s | 27,6 s | 13,4 s | 289 → 134 | 2,8 ms | 4,3 ms |
| 250 (5.000) | 131,0 s | 125,6 s | 35,7 s | 524 → 143 | 7,0 ms | 5,1 ms |
| 500 (10.000) | 413,6 s | 439,2 s | **74,0 s** | 827 → 148 | 15,6 ms | 5,6 ms |
| 1.000 (20.000) | — | — | 151,2 s | 151 | 41,0 ms | 6,4 ms |
| 2.500 (50.000) | — | — | 342,4 s | 137 | 75,4 ms | 5,5 ms |

- **Por lote (directorio) la ingesta pasa a ser lineal** (~140 ms/doc, la mayor parte en Chroma:
  ~45 ms por llamada de inserción, dos por documento). En `main` era cuadrática: 827 ms/doc con
  500 documentos y subiendo; 5,6× más rápida con 500 documentos.
- **Por fichero (subidas por API) queda igual que `main`**: cada subida sigue reconstruyendo el
  BM25 (O(N) por subida). La medición de 500 documentos coincidió con otras pruebas en la máquina.
- **La consulta BM25 crece linealmente** (~1,5 ms por cada 1.000 fragmentos y zona): ~150 ms con
  10⁵ fragmentos y ~1,5 s con 10⁶. La vectorial (HNSW) se mantiene en ~5 ms.
- **Memoria:** el BM25 de la copia `general` ocupa +243 MB con 10.000 fragmentos y +1,2 GB con
  50.000 (~24 KB por fragmento); los de cada zona suman otro tanto.
- **Disco:** 437 MB con 10.000 fragmentos y 2,1 GB con 50.000 (casi la mitad en la colección
  Chroma de la copia `general`). Arranque: 1,0 s y 4,4 s para recargar el BM25 `general`.
- **Conclusión:** el almacenamiento embebido sirve hasta ~10⁵ fragmentos con una máquina holgada;
  más allá hace falta el motor dedicado de la Fase 2.

### 4.2 Pipeline completo con modelos reales (`balanced`, GPU)

| Medida | `main` | Rama |
|---|---|---|
| Evaluación golden set (12 preguntas, incluye ingesta) | 170,9 s | **38,9 s** |
| Evaluación borrador tripulación (47 preguntas) | 416,5 s | **175,9 s** |
| Cargas del reranker / embedder en una evaluación | 16 / 4 | **1 / 1** |
| Consulta sin LLM, mediana (5 preguntas) | 2,9 s (carga del reranker en la 1.ª consulta de cada zona) | **0,13 s** |
| Consulta con LLM 1,5B en **CPU**, mediana | 6,4 s | 4,1 s |
| Métricas del golden set | zona 1,0 · recuperación 1,0 · citas 1,0 · contenido 0,9 · abstención 1,0 | **idénticas** |
| Estados en el borrador de tripulación | 15 ok · 28 no encontrado · 4 abstención | 18 ok · 29 no encontrado · 0 abstención |

Los 4 casos que cambian en el borrador se revisaron a mano: “arranque del generador diésel en
frío” y “extinción CO2” pasan de abstención a **respuestas correctas paso a paso**; “alarmas
off-track en ECDIS” sigue sin respuesta (el corpus no lo cubre), ahora como “no encontrado”;
“qué registrar en el diario de navegación” salió `ok` en la evaluación pero “no encontrado” al
repetirla: con `temperature: 0.1` el LLM no es determinista (motivo para evaluar con `0.0`).

### 4.3 Observaciones de calidad

- Con `lite` (sin modelos) la evaluación es **idéntica caso a caso** antes y después.
- El modelo de 1,5B es insuficiente para redactar con fiabilidad: en una ejecución respondió a
  “presión mínima de aire de arranque” con la presión de **aceite** (dato real del documento, pero
  de otra magnitud). Ningún verificador léxico ni NLI lo detecta. Ver [LLM.md §4.2](LLM.md#42-elegir-un-modelo-más-grande-que-15b).
- Las citas repiten documento (`emergencias_hombre_al_agua` y `emergencias_hombre_al_agua-ec4f30ca`)
  porque el corpus de ejemplo contiene cada documento en `.txt` y en `.pdf`. Antes compartían
  `doc_id`; ahora se distinguen. En una empresa pasará lo mismo con un PDF y su Word: hace falta
  detección de casi-duplicados (Fase 2).

---

## 5. ¿Tiene sentido para la empresa?

### Qué conservar

- Recuperación híbrida + RRF + rerank (los códigos internos y siglas lo necesitan).
- Chunking por estructura del documento y citas con documento/versión/página.
- Verificación + abstención (“no tengo base documental”) y traces.
- Evaluación con golden set y la API compatible con OpenAI (Open WebUI como interfaz).
- El modo extractivo como red de seguridad cuando el LLM falla.

### Qué cambiar (y cuándo)

| Necesidad | Fase |
|---|---|
| Motor de búsqueda dedicado (híbrido, filtros, réplicas) en lugar de ficheros en memoria | F2 |
| Taxonomía propia, formatos Office/HTML/correo, análisis léxico en español, ingesta asíncrona | F2 |
| SSO + **permisos por documento** aplicados en cada búsqueda; conectores con el origen | F3 |
| LLM en servidor de inferencia (o nube) y modelo de 7B o más | F2 (ya soportado en código) |
| Golden set real por área, feedback de usuarios, métricas y alertas | F4 |

### Construir o comprar

Si la empresa trabaja en Microsoft 365 o Google Workspace y no tiene requisitos especiales de
confidencialidad, sus asistentes integrados ya resuelven conectores y permisos. Construir sobre
ShipRAG tiene sentido si se necesita una o más de estas cosas: **despliegue local o sin
internet**, control total de qué se indexa y cómo se responde, **citas verificables con
abstención**, o integrar repositorios que esos productos no cubren. La decisión conviene tomarla
antes de invertir en las Fases 2–3.

### Límites actuales (con la Fase 1)

- Un proceso, un escritor. Consultas en paralelo sí; el LLM en proceso, de una en una.
- **Un solo escritor por índice**: el servidor carga el BM25 y el registro en memoria al arrancar.
  Si se ingiere por CLI con el servidor en marcha, el servidor no lo ve hasta reiniciarse, y dos
  procesos escribiendo a la vez pueden perder cambios (ya ocurría en `main`). Con el servidor
  arrancado, ingerir por la API; la cola de ingesta de la Fase 2 lo resuelve.
- Índice embebido razonable hasta ~10⁴–10⁵ fragmentos (ver §4.1); más allá, Fase 2.
- **No subir documentos restringidos**: todos los usuarios ven todo.

---

## 6. Cómo reproducir estas mediciones

```bash
pytest -q                                                # 57 tests
shiprag --profile lite eval eval/golden_set.jsonl        # calidad sin modelos
shiprag --profile balanced eval eval/golden_set.jsonl    # con modelos (GPU)
python scripts/bench_index.py --docs 50 100 250 500      # índice embebido
shiprag --profile balanced doctor                        # entorno (GPU del LLM, token, NLI…)
```

Para comparar con `main`, ejecutar el mismo `scripts/bench_index.py` con el `src` de `main` en
`PYTHONPATH` (por ejemplo desde un `git worktree`).
