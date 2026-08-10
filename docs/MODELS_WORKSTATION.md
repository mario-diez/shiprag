# Modelos para PC de casa potente (perfil `workstation`)

Hardware de referencia: **~12 GB VRAM + 64 GB RAM**.

## ¿Hacen falta modelos “tochos”?

No necesariamente. Con tu máquina el punto dulce es:

| Pieza | Modelo | ¿Tocho? | Notas |
|---|---|---|---|
| Embeddings | `multilingual-e5-base` | No | Suficiente para ES/EN técnico |
| Reranker | `bge-reranker-base` | No | Mejora mucho precisión@k |
| LLM | **Qwen2.5-7B-Instruct** GGUF Q4/Q5 | Medio | Cabe en 12GB; opcional |

No necesitas un 70B para este caso. Un 7B bien anclado a citas + extractivo en emergencias es más seguro que un modelo enorme inventando.

`lite` = **sin** redes neuronales (solo para probar flujo).  
Con 12GB VRAM usa **`workstation`**, no `lite`.

## Flujo actual del sistema

```
Pregunta
   │
   ▼
Router (zona: puente/máquina/emergencias…)
   │
   ▼
Recuperación híbrida: BM25 + embeddings
   │
   ▼
Reranker (reordena fragmentos)
   │
   ├─ Si emergencia / modo citas ──► respuesta EXTRACTIVA (pega el manual)
   │
   └─ Si normal y llm.enabled ─────► Qwen GGUF reformula SOLO con evidencia
                                      + verifier (si se desvía → abstención)
```

Open WebUI, si lo usas, solo pinta el chat; por detrás sigue este pipeline.

## Arranque recomendado en tu PC

```bash
# 1) Embeddings + reranker
python scripts/download_models.py --profile workstation

# 2) Qwen GGUF (elige UNO; ejemplo Q4_K_M ~4–5GB)
# Descárgalo en puerto (Hugging Face / ollama export / etc.) a:
#   models/llm/qwen2.5-7b-instruct-q4_k_m.gguf
#
# Ejemplos de repos habituales (nombres orientativos):
#   Qwen/Qwen2.5-7B-Instruct-GGUF  →  qwen2.5-7b-instruct-q4_k_m.gguf

# 3) Activar LLM en config/profiles/workstation.yaml
#    models.llm.enabled: true

# 4) Dependencia LLM local
pip install -e ".[llm]"

# 5) Ingestar + servir
shiprag --profile workstation ingest data/sample
shiprag --profile workstation serve --port 8080
```

Si VRAM se queda justa con embeddings+reranker+LLM a la vez:

1. En `workstation.yaml` pon `device: cpu` en embedding/reranker y deja el GGUF en GPU, **o**
2. Baja `n_gpu_layers`, **o**
3. Usa Q4 en vez de Q5/Q6, **o**
4. Deja `llm.enabled: false` y usa solo retrieve+extractivo (sigue siendo muy útil).

## Comparativa rápida de perfiles

| Perfil | Modelos | Para ti |
|---|---|---|
| `lite` | Ninguno | Solo demo rápida sin pesos |
| `home` | e5-small + MiniLM (CPU) | Portátil / sin GPU |
| **`workstation`** | e5-base + bge-reranker + Qwen 7B | **Tu PC (12GB)** |
| `server` | Igual de base, pensado a bordo / más carga | Flota / servidor |

## Sobre “lo de Qwen que montaste antes”

Encaja así:

- Antes: Open WebUI → Ollama/Qwen (chat general, más riesgo de inventar).  
- Ahora: Open WebUI (opcional) → **ShipRAG** → (retrieve + citas) → Qwen solo si está enabled y **no** en emergencia.

Es decir: Qwen deja de ser “el cerebro suelto” y pasa a ser **redactor anclado a manuales**.
