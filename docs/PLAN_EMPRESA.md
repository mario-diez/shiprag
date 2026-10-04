# Plan: de ShipRAG a asistente documental de empresa

**Fecha:** 2026-10-04 · **Rama de la Fase 1:** `feat/fundamentos-escalado`
**Documentos relacionados:** [AUDITORIA_ESCALADO.md](AUDITORIA_ESCALADO.md) (lo que hay y lo que falla) ·
[LLM.md](LLM.md) (modelos, servido e inferencia) · [NOVEDADES.md](NOVEDADES.md) (cambios aplicados)

---

## 1. Objetivo

Una empresa con mucha documentación interna (procedimientos, manuales, normativa, ofertas,
documentación técnica…) quiere **preguntar en lenguaje natural y recibir una respuesta con la
cita exacta** (documento, versión, página), sin tener que saber dónde está cada cosa.

ShipRAG ya resuelve bien la parte difícil (recuperación híbrida, citas, abstención cuando no hay
evidencia). Lo que no resuelve todavía es **el volumen, los permisos y la conexión con los
repositorios donde vive la documentación**. Este plan ordena ese trabajo.

### 1.1 Requisitos de una empresa (lo que el sistema debe cumplir al final)

| Requisito | Nivel objetivo (orientativo, a validar con la empresa) | Estado tras Fase 1 |
|---|---|---|
| Volumen | 10⁴–10⁵ documentos · 10⁵–10⁶ chunks | Embebido válido hasta ~10⁴–10⁵ chunks (ver auditoría §4) |
| Usuarios concurrentes | decenas | Un proceso; consultas en paralelo, LLM serializado |
| Latencia | p95 < 2 s sin LLM · < 8 s con LLM | Sin LLM < 1 s en el corpus de prueba |
| Frescura | cambio visible en minutos; borrados respetados | Reingesta sustituye versión; `--prune` sincroniza borrados |
| Permisos | cada usuario solo ve lo que puede ver en el origen | **No** (solo token de administración para escribir) |
| Formatos | PDF, Word, Excel, PowerPoint, HTML, correo | PDF, TXT/MD, imagen (OCR) |
| Orígenes | SharePoint/OneDrive, Google Drive, Confluence, carpetas | Carpetas locales y subida por API |
| Auditoría | quién preguntó qué y con qué fuentes | Traces JSONL diarios (sin identidad de usuario) |
| Privacidad | retención definida, datos en la UE / on-prem | Retención configurable; todo local |
| Calidad | medida con preguntas reales de cada área | Golden set sintético del buque |

### 1.2 Lo que se mantiene (no negociable)

1. **Responder solo con evidencia** y citarla; abstenerse antes que inventar.
2. **Recuperación híbrida** (léxica + semántica): los códigos internos (`PR-0042`, `FO-12`) no se
   encuentran bien solo con vectores.
3. **Verificación posterior** a la generación (léxica ± NLI) y traza auditable.
4. **Evaluación con golden set** antes de cambiar modelos o umbrales.
5. **El LLM es opcional y sustituible**: el sistema funciona en modo extractivo sin él.

---

## 2. Arquitectura objetivo

```
 Orígenes                    Ingesta (asíncrona)                    Búsqueda
┌──────────────────┐   ┌─────────────────────────────────┐   ┌───────────────────────────┐
│ SharePoint/Drive │──▶│ Conectores: cambios (delta)     │   │ Motor híbrido             │
│ Confluence / NAS │   │ + permisos de cada documento    │   │ · BM25 con analizador ES  │
│ Subidas API      │   │        ↓ cola de trabajos       │──▶│ · vectores (+ sparse)     │
└──────────────────┘   │ Workers: extraer → trocear →    │   │ · filtros: área, tipo,    │
                       │ embeddings (GPU) → upsert/borrar│   │   PERMISOS (obligatorio)  │
                       └─────────────────────────────────┘   └─────────────▲─────────────┘
                                                                          │
 Usuarios (SSO)          API de consulta (N réplicas, sin estado)          │
┌──────────────────┐   ┌─────────────────────────────────┐                │
│ Open WebUI / UI  │──▶│ identidad → reescritura de      │────────────────┘
│ Teams / web      │   │ seguimiento → recuperación      │
└──────────────────┘   │ filtrada por permisos → rerank  │──▶ Servidor LLM (vLLM / TGI / cloud)
                       │ → LLM anclado → verificador →   │──▶ Reranker / NLI (GPU)
                       │ respuesta + citas + trace       │──▶ Traces · métricas · feedback
                       └─────────────────────────────────┘
```

Diferencias clave con lo que hay hoy:

| Hoy (ShipRAG) | Objetivo |
|---|---|
| Chroma + BM25 en ficheros, por zona, con copia “general” de todo | Un motor de búsqueda con una colección y filtros por metadatos |
| Zonas del buque fijas en código (`Zone` enum) | Taxonomía configurable (áreas, departamentos, tipos) |
| Ingesta síncrona en el proceso de la API | Cola + workers; la API solo encola y consulta |
| LLM dentro del proceso (llama.cpp) | Servidor de inferencia aparte (ya soportado: `llm.backend: openai_compat`) |
| Sin identidad de usuario | SSO + permisos heredados del origen aplicados en cada búsqueda |

---

## 3. Fases

Tamaño orientativo: **S** ≈ días · **M** ≈ 1–3 semanas · **L** ≈ 1–2 meses (1–2 personas).

### Fase 1 — Fundamentos (HECHA en `feat/fundamentos-escalado`)

Objetivo: que lo que hay sea **correcto y seguro** al crecer, sin cambiar la arquitectura.

| # | Cambio | Resuelve | Dónde |
|---|---|---|---|
| 1.1 | Reingesta **sustituye** la versión anterior; registro de documentos con identidad estable | Respuestas con versiones viejas mezcladas | `ingest/pipeline.py`, `ingest/registry.py`, `index/store.py` |
| 1.2 | Ingesta **incremental** (huella de contenido + config); `--force` | Reprocesar todo cada vez | `ingest/pipeline.py` |
| 1.3 | **Borrado** (`shiprag delete`, `DELETE /api/documents/{id}`) y **sincronización** (`ingest --prune`) | Documentos borrados que seguían respondiendo | `cli.py`, `api/app.py` |
| 1.4 | Sin colisiones de nombre (`manual.pdf` en dos carpetas) ni en `data/raw` | Un documento pisaba a otro | `ingest/pipeline.py` |
| 1.5 | Embeddings calculados **una vez** (antes ×2 por la copia general) | Doble coste de GPU | `index/store.py` |
| 1.6 | BM25: **un rebuild por lote** al ingerir un directorio, snapshot inmutable para lecturas, escritura atómica | Ingesta cuadrática; lecturas a medio actualizar; corrupción | `index/lexical.py` |
| 1.7 | **Caché de modelos** por proceso (reranker, embedder, NLI, LLM) | Reranker cargado una vez por zona (16 cargas en una evaluación) | `core/model_cache.py` |
| 1.8 | API: subida saneada (path traversal), límite de tamaño, tipos permitidos, **token de administración** para escribir/borrar, trabajo pesado fuera del event loop, inicialización sin carreras | Seguridad básica y bloqueos | `api/app.py`, `api/openai_compat.py` |
| 1.9 | LLM: plantilla de chat, evidencia completa con presupuesto, citas numeradas validadas, limpieza de `<think>`/bucles, “NO ENCONTRADO” → abstención, respuestas vacías → extractivo, **backend HTTP** (vLLM/Ollama/cloud), bloqueo en llama.cpp | Respuestas basura aceptadas; LLM no escalable | `generation/generator.py` |
| 1.10 | Chat: preguntas de **seguimiento** reescritas como consulta autónoma; la emergencia se hereda | “¿y si no arranca?” no recuperaba nada | `generation/condense.py` |
| 1.11 | Override de emergencia **configurable** (`router.emergency_patterns: []` para oficina) | “plan de emergencia” forzaba modo solo-citas | `experts/router.py`, `config/default.yaml` |
| 1.12 | Traces JSONL diarios con retención; `doctor` detecta llama.cpp sin GPU y escritura sin token | Miles de ficheros; diagnóstico | `core/logging.py`, `doctor.py` |
| 1.13 | Benchmark reproducible del índice | Decidir con datos cuándo cambiar de motor | `scripts/bench_index.py` |

Criterios de aceptación cumplidos: 57 tests (antes 13); evaluación `lite` idéntica caso a caso y
`balanced` con las mismas métricas agregadas; mediciones antes/después en la auditoría.

### Fase 2 — Escala de datos y dominio genérico (L)

Objetivo: soportar el corpus completo de la empresa y quitar lo específico del buque del núcleo.

| # | Tarea | Notas |
|---|---|---|
| 2.1 | Interfaz `SearchBackend` (upsert, borrar por documento, búsqueda híbrida con filtros) y adaptador del almacenamiento actual | El modo buque/offline sigue funcionando igual |
| 2.2 | Adaptador del motor elegido (decisión D1) con **una sola colección** y `area`/`zona` como filtro | Elimina la copia “general” (½ almacenamiento) |
| 2.3 | Taxonomía configurable: `Zone` deja de ser un enum fijo; pistas de inferencia (`ZONE_HINTS`) y ejemplos del router en YAML | Perfil `empresa` con áreas propias |
| 2.4 | Formatos Office/HTML/correo: DOCX (títulos → secciones), XLSX (tablas por hoja), PPTX (por diapositiva), HTML, EML/MSG | Evaluar `docling`/`unstructured` frente a extractores propios |
| 2.5 | Ingesta asíncrona: cola + workers, estado de trabajos, reintentos; la API solo encola | La subida responde al momento |
| 2.6 | Análisis léxico en español (stemming, stopwords, sinónimos y siglas de la empresa) | Hoy “procedimientos” ≠ “procedimiento” en BM25 |
| 2.7 | Reindexado sin corte (colección nueva + alias) y versionado de embeddings | Cambiar de modelo sin parar el servicio |
| 2.8 | Detección de casi-duplicados (mismo documento en PDF y Word, o copiado en varias carpetas) | Hoy aparecen citas repetidas (en el corpus de ejemplo, `x.txt` y `pdf/x.pdf`) |

Aceptación: corpus sintético de 10⁶ chunks; p95 de búsqueda < 300 ms; ingesta ≥ 1.000 chunks/min
por worker con GPU; un borrado deja de responder en < 1 min.

### Fase 3 — Identidad, permisos y conectores (L)

Objetivo: que cada persona vea solo lo que puede ver en el origen. **Requisito para producción.**

| # | Tarea | Notas |
|---|---|---|
| 3.1 | SSO OIDC en la API (Entra ID / Google / Keycloak) | El token de administración de Fase 1 queda solo para máquinas |
| 3.2 | Identidad desde Open WebUI | Open WebUI con OIDC y reenvío de cabeceras de usuario hacia ShipRAG; red aislada o secreto compartido para que nadie más pueda suplantar cabeceras |
| 3.3 | **ACL por documento**: `allowed_principals` (usuarios/grupos) en metadatos; filtro **obligatorio** dentro del motor (pre-filtro, nunca post-filtro); denegar por defecto | Tests específicos de fuga entre usuarios |
| 3.4 | Conectores con permisos y cambios incrementales: SharePoint/OneDrive (Graph), Google Drive, Confluence | Prioridad según dónde esté la documentación |
| 3.5 | Auditoría por usuario y RGPD: retención, acceso/supresión en traces, minimización | Consultas = posibles datos personales |

Aceptación: batería de tests de permisos (usuario A no recupera **nunca** chunks de B), auditoría
revisada por seguridad/legal.

### Fase 4 — Calidad y operación continua (M, recurrente)

| # | Tarea |
|---|---|
| 4.1 | Golden set por área con preguntas **reales** (≥ 50 por área) y respuesta/cita esperada |
| 4.2 | Feedback 👍/👎 en la interfaz → cola de revisión → nuevos casos de evaluación |
| 4.3 | Evaluación en CI: ningún cambio de modelo/umbral/prompt sin comparar métricas |
| 4.4 | Métricas y alertas: latencia por etapa, tasa de abstención, documentos sin indexar, errores de ingesta |
| 4.5 | Backups de índice y registro; 2+ réplicas de la API; runbook de “el asistente no responde” |

---

## 4. Decisiones que debe tomar el equipo

### D1 — Motor de búsqueda (bloquea Fase 2)

| Criterio | OpenSearch / Elasticsearch | Qdrant | PostgreSQL + pgvector |
|---|---|---|---|
| Léxico en español (stemming, sinónimos) | **Excelente** (analizadores Lucene) | Vía vectores *sparse* (BM25/BGE-M3), menos configurable | Bueno (`tsvector` `spanish`) |
| Vectores + híbrido | Sí (kNN + fusión) | **Excelente** (denso + sparse + fusión nativa) | Sí (HNSW), fusión a mano |
| Filtros / permisos | Sí; seguridad a nivel de documento con plugin | Sí (filtros de payload indexados) | Sí (WHERE / Row Level Security) |
| Operación | Pesada (JVM, clúster) | **Ligera** (un binario, modo embebido para tests) | Ligera si ya hay Postgres |
| Encaje | Mucho texto, siglas, búsqueda léxica crítica | Prioridad semántica y simplicidad | Ya operan Postgres; corpus moderado |

**Recomendación:** decidir con una prueba de concepto de ~2 semanas sobre una muestra real
(10–20 % del corpus) midiendo *recall@10* con el golden set. A falta de datos, OpenSearch si la
búsqueda por códigos/siglas en español es crítica; Qdrant si se prioriza simplicidad.

### D2 — LLM local o en la nube

Ver [LLM.md](LLM.md) §5. Resumen: el sistema ya admite ambos sin cambiar código
(`llm.backend: openai_compat`). Local (vLLM en GPU propia) si la documentación es
confidencial o hay requisitos de residencia; nube si la política lo permite y se prioriza calidad
con poco hardware. En ambos casos, el modo extractivo sigue disponible como red de seguridad.

### D3 — Interfaz

Open WebUI ya funciona contra ShipRAG (`/v1/chat/completions`) y admite SSO: es la opción
más rápida. Integración con Teams/Slack, si se quiere, va después de Fase 3 (necesita identidad).

### D4 — ¿Un producto o dos?

Recomendación: **un repositorio con perfiles** (`buque`/offline y `empresa`), compartiendo
núcleo (recuperación, verificación, evaluación). Lo específico del buque (zonas, patrones de
emergencia, modo solo-citas) pasa a configuración en la Fase 2.

---

## 5. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Fuga de información entre usuarios | ACL como pre-filtro en el motor + tests de fuga obligatorios en CI (Fase 3) |
| Respuestas “plausibles” pero falsas con un LLM pequeño | Verificador + abstención; elegir modelo con el golden set; ver [LLM.md](LLM.md) |
| Documentación desactualizada en el índice | Conectores incrementales + `--prune`; mostrar fecha de ingesta en la cita |
| Calidad pobre en documentos escaneados/tablas | OCR y extractores específicos; medir por tipo de documento |
| Coste de GPU | Empezar con 7–8B cuantizado; medir uso real antes de dimensionar |
| Expectativas (“que sepa todo”) | Comunicar que responde **solo** con lo indexado y cita la fuente |
