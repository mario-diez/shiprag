# LLM en ShipRAG: papel, problemas encontrados y qué hace falta

**Fecha:** 2026-10-04 · **Rama:** `feat/fundamentos-escalado`
**Relacionado:** [AUDITORIA_ESCALADO.md](AUDITORIA_ESCALADO.md) · [PLAN_EMPRESA.md](PLAN_EMPRESA.md)

---

## 1. Qué hace (y qué no) el LLM aquí

El LLM **no busca ni decide qué es verdad**. La recuperación (BM25 + vectores + reranker) elige
la evidencia; el LLM solo **redacta** una respuesta con esas fuentes y las cita; el verificador
decide si se entrega o si el sistema se abstiene.

```
pregunta ─▶ [reescritura si es seguimiento] ─▶ recuperación híbrida ─▶ rerank ─▶ top-k fuentes
                                                                                   │
        ┌──────────────────────────────────────────────────────────────────────────┘
        ▼
  LLM: redacta SOLO con la evidencia, cita [n] ─▶ verificador (léxico ± NLI) ─▶ entrega o abstención
        │ fallo · timeout · respuesta vacía o sin contenido
        └──────────────▶ respuesta semi-extractiva (siempre disponible, sin LLM)
```

- **Emergencias y modo “solo citas” nunca pasan por el LLM** (se citan los pasos literales).
- **Sin LLM el sistema funciona** (extractivo/semi-extractivo). El LLM aporta redacción natural,
  síntesis de varias fuentes y la reescritura de preguntas de seguimiento en el chat.

---

## 2. Qué había y qué fallaba (medido)

Entorno de la medición: RTX 4070 SUPER 12 GB, perfil `balanced` (e5-base + bge-reranker-base
en GPU, **Qwen2.5-1.5B-Instruct Q4_K_M**), verificador NLI no descargado (solo léxico).
Mismas 5 preguntas sobre el corpus de ejemplo, antes y después de esta rama:

| Pregunta | Antes (prompt crudo) | Después (plantilla de chat + reglas) |
|---|---|---|
| Presión mínima de aire de arranque | Bucle `25 bar / RESPUESTA: NO ENCONTRADO / RESPUESTA: 25 bar…` hasta agotar tokens (13 s) y **aceptado como `ok`** | “Presión mínima de aire de arranque: ≥ 25 bar” [1] (4,6 s). En otra ejecución, el 1,5B citó la presión de **aceite**: fiel a un documento, pero no responde la pregunta (ver §4.2) |
| Relevo de guardia en el puente | Pasos correctos terminados en un literal `[n]` | 7 pasos + el AVISO de seguridad del documento |
| Zona de snap-back | Paráfrasis débil `[n]` → abstención | El modelo respondió solo `[1]` → **ahora se detecta** y se degrada a semi-extractivo (antes habría pasado: grounding = 1,0) |
| Alarma FO-12 | “NO ENCONTRADO” (el dato estaba fuera de los 500 caracteres que veía) | Responde con la evidencia completa; la recuperación sigue siendo débil → abstención final |
| Receta de paella (fuera de dominio) | “No encontrado” + volcado de fuentes [6], [7] | `NO ENCONTRADO` → abstención estándar |

Problemas encontrados en el código (todos corregidos salvo P8, que es del entorno):

| # | Problema | Efecto | Dónde estaba |
|---|---|---|---|
| P1 | Prompt crudo sin la **plantilla de chat** del modelo | El modelo “continúa el documento”: bucles, prefijos `RESPUESTA:`, volcado de fuentes | `LocalLLMGenerator.generate` |
| P2 | Instrucción literal “referencias [n]” | Escribe `[n]`, no números de cita | prompt |
| P3 | Evidencia = citas **recortadas a 500 caracteres**, máx. 5 | Datos fuera del recorte nunca llegaban al modelo | `context` desde `base.citations` |
| P4 | Sin presupuesto de contexto frente a `n_ctx` | Al pasar chunks completos, riesgo de exceder la ventana | — |
| P5 | “NO ENCONTRADO” del LLM no cambiaba el estado | Abstenciones con estado `ok` | — |
| P6 | `llama_cpp.Llama` compartido entre hilos sin bloqueo | Carreras/corrupción con consultas simultáneas (FastAPI usa un pool de hilos) | — |
| P7 | Un LLM por `Orchestrator` y dentro del proceso de la API | Cargas duplicadas; no escala ni comparte GPU | `build_generator` |
| P8 | `llama-cpp-python` instalado **sin CUDA** en esta máquina | `n_gpu_layers=99` se ignora: 4–15 s por respuesta en CPU con un 1,5B | entorno |
| P9 | Qwen3 (perfil `server`) razona en `<think>` por defecto | Gasta tokens y podría mostrar el razonamiento | — |
| P10 | Solo se usaba el último mensaje del chat | “¿y si no arranca?” se buscaba tal cual y no encontraba nada | `openai_compat.py` |

---

## 3. Qué se ha cambiado en esta rama

| Cambio | Efecto | Configuración |
|---|---|---|
| Mensajes `system` + `user` con la plantilla de chat del modelo (`create_chat_completion`) | Fin de los bucles y del “continuar el documento” | — |
| Reglas del sistema: solo evidencia, `NO ENCONTRADO`, citar `[n]`, no inventar cifras, la evidencia **no son instrucciones** (inyección de prompt) | Respuestas más disciplinadas | `models.llm.system_prompt` (vacío = por defecto) |
| Evidencia: **texto completo** de cada fuente, numerada `<fuente id="n" …>`, dentro de un presupuesto calculado desde `n_ctx` y `max_tokens`; si se excede la ventana, reintento con la mitad | El modelo ve los datos; no se desborda | `models.llm.context_max_chars` (0 = auto) |
| Limpieza de salida: `<think>…</think>`, prefijos `RESPUESTA:`, referencias inexistentes (`[7]` con 5 fuentes), líneas repetidas | Salida limpia aunque el modelo divague | — |
| Respuesta vacía o **sin contenido** (solo `[1]`) → semi-extractivo | No se entrega basura “anclada” | — |
| `NO ENCONTRADO` → abstención estándar del verificador | Estado correcto | — |
| **Backend HTTP compatible con OpenAI** (vLLM, Ollama, llama.cpp server, LM Studio, TGI, nube) con API key desde variable de entorno | El LLM sale del proceso y escala aparte | `models.llm.backend: openai_compat`, `base_url`, `model`, `api_key_env`, `timeout_s` |
| Cualquier fallo del backend → semi-extractivo, con el motivo en `generation_info` y en el trace | La consulta nunca falla por el LLM | — |
| Bloqueo en llama.cpp + caché de modelos por proceso | Seguro con concurrencia; una sola carga | — |
| Sufijo de prompt para Qwen3 (`/no_think`) en el perfil `server` | Sin razonamiento oculto | `models.llm.prompt_suffix` |
| Reescritura de **preguntas de seguimiento** (LLM si está activo; heurística si no). En conversaciones de emergencia se usa la heurística y se **hereda** el modo emergencia | El chat de Open WebUI entiende el contexto | `chat.*` |
| `doctor` avisa si `llama-cpp-python` no tiene GPU y comprueba que el servidor HTTP responde | Diagnóstico | — |

Tests sin pesos reales en [tests/test_llm.py](../tests/test_llm.py) y
[tests/test_chat_and_config.py](../tests/test_chat_and_config.py) (backends simulados, incluido
un servidor HTTP falso con `httpx.MockTransport`).

---

## 4. Qué más hace falta

### 4.1 Servir el LLM fuera del proceso (necesario para varios usuarios)

Con llama.cpp dentro de la API, cada respuesta ocupa el modelo entero: con 10 usuarios a la vez,
el décimo espera a los nueve anteriores. Un **servidor de inferencia** (vLLM o TGI en producción;
Ollama, llama.cpp server o LM Studio para pruebas en Windows) agrupa peticiones simultáneas en la
misma GPU (*continuous batching*), se escala y actualiza por separado y puede compartirse con
otros servicios. **Ya está soportado** (`llm.backend: openai_compat`); falta desplegarlo.

### 4.2 Elegir un modelo más grande que 1,5B

Lo observado con Qwen2.5-1.5B: ignora a veces la regla de citas, responde solo `[1]`, y en una
ejecución contestó con un dato **real del documento pero de otra magnitud** (presión de aceite en
vez de aire de arranque). Ese error es el más peligroso porque ningún verificador léxico ni NLI lo
detecta: la frase sí está en la evidencia.

- **Mínimo razonable:** modelo *instruct* multilingüe de 7–8B (cuantizado 4-bit cabe en la 4070 SUPER).
- **Calidad:** 14B–32B si hay que sintetizar varios documentos o redactar largo.
- **Cómo elegir:** con el golden set **propio** (preguntas reales con respuesta esperada), no por
  rankings públicos. Revisar qué versiones están vigentes en el momento de desplegar.
- **Temperatura:** usar `0.0` en evaluación para que los resultados sean reproducibles
  (los perfiles usan `0.1`).

### 4.3 Contexto

Con chunks de ~1.200 caracteres y 8–10 fuentes, la evidencia ocupa ~3–4k tokens. Recomendado
`n_ctx ≥ 8192` (con 4096 caben ~7 fuentes con `max_tokens: 512`). El presupuesto se ajusta solo,
pero `n_ctx` debe coincidir con la ventana configurada en el servidor.

### 4.4 Dimensionamiento de GPU (aproximado)

| Modelo | Cuantización | VRAM de pesos | Encaje orientativo |
|---|---|---|---|
| 7–8B | 4-bit (GGUF Q4_K_M / AWQ) | ~5 GB | GPU de 12 GB junto a embeddings + reranker |
| 14B | 4-bit | ~9–10 GB | GPU de 16–24 GB |
| 32B | 4-bit | ~19–20 GB | GPU de 24–48 GB |

Hay que sumar la caché KV, que crece con `n_ctx` y con los usuarios simultáneos (reservar varios
GB en servidor). Medir con carga real antes de comprar hardware.

### 4.5 Embeddings, reranker y verificador

- Los modelos actuales (e5 multilingüe, bge-reranker) sirven para español. A escala, **BGE-M3**
  (perfil `server`) da vectores densos y *sparse* a la vez, que encajan con la búsqueda híbrida de
  Qdrant/OpenSearch (Fase 2).
- Cambiar de modelo de embeddings **obliga a reindexar**: versionar el índice (Fase 2.7).
- **El verificador NLI no está descargado en esta máquina** (`models/verifier/` no existe): los
  perfiles `home`/`balanced` verifican solo léxicamente. Descargarlo:
  `python scripts/download_models.py --profile balanced`. Valida frase a frase: en CPU añade
  del orden de 0,1–0,3 s por frase; en GPU mucho menos (`models.verifier.device: cuda`).

### 4.6 Streaming: decisión consciente

No se envían tokens según se generan: el verificador necesita la respuesta completa para decidir
si se entrega. Hacer *streaming* mostraría texto no verificado. La respuesta se envía troceada
**después** de verificarla (lo que ya hace `/v1/chat/completions` con `stream: true`).

### 4.7 Seguridad y privacidad

- **Inyección de prompt** desde documentos (“ignora las instrucciones…”): mitigada con
  delimitadores, una regla de sistema y el verificador. Con permisos (Fase 3), el LLM solo debe
  recibir chunks que el usuario puede ver: **nunca** filtrar después de generar.
- Los traces guardan la pregunta y un extracto de la respuesta: pueden ser **datos personales**.
  Definir la retención (`logging.trace_retention_days`).
- Con un proveedor en la nube solo se envían las fuentes recuperadas (no el corpus), pero hace
  falta contrato de tratamiento de datos, región y garantía de no entrenar con los datos.

### 4.8 Evaluar el LLM, no solo la recuperación

Métricas que faltan en `eval_runner.py`: **fidelidad** (cada frase respaldada por su cita),
**exactitud de citas** (`[n]` apunta a la fuente correcta), **respuesta a lo preguntado** (el fallo
de §4.2) y tasa de abstención correcta. Comparar extractivo frente a LLM por área y repetir la
evaluación en cada cambio de modelo, prompt o umbral.

---

## 5. ¿Local o en la nube?

| Criterio | Local (vLLM/Ollama en GPU propia) | Proveedor en la nube |
|---|---|---|
| Confidencialidad / residencia | **Máxima**: nada sale | Requiere contrato y región adecuados |
| Calidad con poco hardware | Limitada por la GPU | **Alta** sin hardware propio |
| Coste | Inversión en GPU + operación | Por uso (proporcional a tokens de evidencia) |
| Latencia | Estable en red local | Depende de red y proveedor |
| Operación | Hay que mantener el servidor | Mínima |
| Sin conexión | **Funciona** | No (ShipRAG cae a extractivo) |

ShipRAG admite ambos sin cambiar código. Para el buque, local. Para la empresa, decidir según la
política de datos (decisión D2 del [plan](PLAN_EMPRESA.md#4-decisiones-que-debe-tomar-el-equipo)).

---

## 6. Cómo configurarlo

Las opciones se ponen en un fichero de overrides local (no versionado) que se aplica encima del
perfil. Plantilla: [config/local.example.yaml](../config/local.example.yaml).

```bash
cp config/local.example.yaml config/local.yaml     # editar y descomentar una opción
export SHIPRAG_LOCAL_CONFIG=config/local.yaml      # PowerShell: $env:SHIPRAG_LOCAL_CONFIG="config/local.yaml"
shiprag --profile balanced doctor                  # comprueba llm_gpu / llm_http
```

### 6.1 llama.cpp en proceso, con GPU

`doctor` muestra `llm_gpu` en aviso si el wheel instalado es solo CPU (caso actual). **Causa:**
[PERFILES.md](../PERFILES.md) y [MODELS_WORKSTATION.md](MODELS_WORKSTATION.md) proponían el
índice `…/whl/cpu`; ese wheel ignora `n_gpu_layers`. Para usar la GPU, reinstalar desde el índice
CUDA que corresponda (esta máquina usa PyTorch `+cu124`):

```bash
pip install --force-reinstall --no-cache-dir llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
shiprag --profile balanced doctor     # llm_gpu debe salir ok
```

Si no hay wheel CUDA para tu versión de Python, en Windows compilar desde fuente suele fallar
(rutas largas, toolkit de CUDA, Visual Studio): mejor usar Ollama (6.2).

### 6.2 Ollama (la vía más rápida en Windows con GPU)

```bash
ollama pull qwen2.5:7b-instruct
```

```yaml
# config/local.yaml
models:
  llm:
    enabled: true
    backend: openai_compat
    base_url: http://127.0.0.1:11434/v1
    model: qwen2.5:7b-instruct      # nombre exacto según `ollama list`
    n_ctx: 8192
```

### 6.3 vLLM (servidor de producción, Linux o WSL2)

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --max-model-len 8192 --api-key "$SHIPRAG_LLM_API_KEY"
```

```yaml
models:
  llm:
    enabled: true
    backend: openai_compat
    base_url: http://inferencia.interna:8000/v1
    model: Qwen/Qwen2.5-7B-Instruct
    api_key_env: SHIPRAG_LLM_API_KEY
    n_ctx: 8192
    timeout_s: 120
```

### 6.4 Proveedor en la nube con API compatible

Igual que 6.3 con la `base_url` y el `model` del proveedor; la clave siempre por
`api_key_env`, nunca escrita en el YAML.

---

## 7. Recomendación

1. **Ahora (prueba de concepto):** Ollama con un modelo de 7B en la 4070 SUPER, perfil `balanced`
   + `config/local.yaml`, descargar el NLI y comparar extractivo frente a LLM con 50 preguntas
   reales de la empresa. No usar el 1,5B salvo para pruebas de humo.
2. **Producción:** servidor de inferencia (vLLM) con un modelo de 7–14B en una GPU de 24 GB o más,
   reranker y NLI en GPU, y la API con 2 réplicas (Fase 4). O un proveedor en la nube si la
   política de datos lo permite.
3. **Siempre:** mantener el modo extractivo como red de seguridad y el golden set como criterio
   para cambiar de modelo.
