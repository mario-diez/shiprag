# ShipRAG — cómo lanzar cada perfil

Hay **5 perfiles** de hardware. Elige uno según tu máquina. Cada perfil guarda índices en su propia carpeta (`data/indexes/<perfil>/`) para no mezclar embeddings distintos.

| Perfil | Hardware | Modelos | GPU / VRAM |
|---|---|---|---|
| `lite` | Cualquier PC | Ninguno | No |
| `home` | Portátil / CPU | e5-small + MiniLM | No |
| **`balanced`** | **PC con GPU 8–12 GB (recomendado si VRAM justa)** | e5-base + bge-reranker, **sin LLM** | ~1–2 GB |
| `workstation` | PC casa potente | e5-base + bge-reranker + Qwen 7B opcional | ~8–12 GB si LLM |
| `server` | Servidor a bordo | e5-base + bge-reranker + GGUF opcional | Opcional |

Comandos comunes (tras instalar):

```bash
shiprag profiles                          # listar perfiles
shiprag --profile <perfil> runtime        # backends activos
shiprag --profile <perfil> doctor         # diagnóstico
```

Fijar perfil por entorno (opcional):

```bash
# Linux / macOS
export SHIPRAG_PROFILE=lite

# Windows PowerShell
$env:SHIPRAG_PROFILE = "lite"
```

---

## 0. Instalación base (una vez)

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```powershell
# Windows
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Requisito: **Python 3.11+**.

---

## 1. `lite` — demo rápida (default)

Sin pesos neuronales. Solo BM25 + hash + respuestas extractivas. Ideal para probar UI, routing, citas y abstención.

### Arranque manual

```bash
shiprag --profile lite ingest data/sample
shiprag --profile lite smoke
shiprag --profile lite query "procedimiento hombre al agua" --emergency
shiprag --profile lite serve --port 8080
```

UI: http://127.0.0.1:8080

### Script automático

- Linux / macOS: `bash scripts/start_lite.sh`
- Windows CMD: `scripts\start_lite.bat`
- Windows PowerShell: `.\scripts\start_lite.ps1`

No hace falta descargar modelos.

---

## 2. `home` — PC / portátil en CPU

Modelos pequeños locales. Si no están descargados, cae a hash/lexical sin romper. LLM desactivado por defecto.

### Descargar modelos (con internet, una vez)

```bash
python scripts/download_models.py --profile home
```

Quedan en:

- `models/embeddings/multilingual-e5-small`
- `models/reranker/ms-marco-MiniLM-L-6-v2`

### Arranque

```bash
shiprag --profile home ingest data/sample
shiprag --profile home doctor
shiprag --profile home serve --port 8080
```

Consulta CLI:

```bash
shiprag --profile home query "alarma FO-12 del generador"
```

OCR opcional (Tesseract en el sistema): ya viene `ocr.enabled: true` en el YAML.

---

## 3. `balanced` — punto intermedio (recomendado con 12 GB justos)

Misma calidad de retrieve que `workstation` (e5-base + bge-reranker), pero:

- embeddings en **GPU** (rápido, poco VRAM)
- reranker en **CPU**
- **sin LLM** → no pelea por los 12 GB

En emergencias sigue siendo extractivo (citas del manual). No necesitas `pip install -e ".[llm]"`.

### Descargar modelos

Si ya corriste `--profile workstation`, **reutiliza los mismos pesos** (mismas rutas bajo `models/`). Si no:

```bash
python scripts/download_models.py --profile balanced
```

### Arranque

```bash
shiprag --profile balanced ingest data/sample
shiprag --profile balanced doctor
shiprag --profile balanced serve --port 8080
```

Sin CUDA: en `config/profiles/balanced.yaml` cambia `device: cuda` → `device: cpu` en embedding.

### Probar un LLM ligero (recomendado antes del 7B)

No hace falta el 7B para ver el flujo generative. Con ~1–2 GB VRAM basta:

| GGUF de prueba | VRAM aprox. | Uso |
|---|---|---|
| **Qwen2.5-1.5B-Instruct Q4_K_M** | ~1 GB | Ideal para probar |
| Qwen2.5-3B-Instruct Q4_K_M | ~2 GB | Un poco mejor redacción |
| Qwen2.5-7B-Instruct Q4_K_M | ~5 GB | Calidad “seria” (perfil workstation) |

1. Descarga el GGUF (p.ej. desde el repo HF `Qwen/Qwen2.5-1.5B-Instruct-GGUF`) a:
   `models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf`
2. Instala el wheel de `llama-cpp-python` (ver sección Windows abajo).
3. En `config/profiles/balanced.yaml`:

```yaml
models:
  llm:
    enabled: true
```

En emergencias sigue mandando el modo extractivo; el LLM solo actúa en consultas normales.

---

## 4. `workstation` — PC de casa con GPU + LLM opcional

Perfil completo: embeddings + reranker en CUDA y Qwen GGUF opcional. Con 12 GB puede quedar **justo** si cargas todo a la vez; si te pasa, usa `balanced` o mueve emb/rerank a CPU.

Detalle de modelos: [docs/MODELS_WORKSTATION.md](docs/MODELS_WORKSTATION.md)

### 1) Descargar embeddings + reranker

```bash
python scripts/download_models.py --profile workstation
```

### 2) (Opcional) LLM GGUF

1. Descarga un GGUF, p.ej. `qwen2.5-7b-instruct-q4_k_m.gguf`, a:
   `models/llm/qwen2.5-7b-instruct-q4_k_m.gguf`
2. Instala `llama-cpp-python` (ver **Instalar LLM en Windows** más abajo).
3. En `config/profiles/workstation.yaml` pon `models.llm.enabled: true`.

Si VRAM se queda justa:

1. Pon `device: cpu` en embedding/reranker y deja el GGUF en GPU, **o**
2. Baja `n_gpu_layers`, **o**
3. Usa Q4 (no Q5/Q6), **o**
4. Quédate en `balanced` sin LLM.

### 3) Arranque

```bash
shiprag --profile workstation ingest data/sample
shiprag --profile workstation doctor
shiprag --profile workstation serve --port 8080
```

---

## 5. `server` — a bordo / alto cómputo

Misma familia de modelos que workstation, pensado para operación continua. Por defecto embeddings/reranker en **CPU**; activa CUDA si hay GPU en el servidor.

### Descargar modelos (en puerto, antes de zarpar)

```bash
python scripts/download_models.py --profile server
```

### GPU (opcional)

En `config/profiles/server.yaml`:

```yaml
models:
  embedding:
    device: cuda
  reranker:
    device: cuda
  llm:
    enabled: true          # solo si tienes GGUF en models/llm/model.gguf
    n_gpu_layers: 35       # >0 si llama-cpp con CUDA/Metal
```

### Arranque

```bash
shiprag --profile server ingest data/sample   # o tu corpus real
shiprag --profile server doctor
shiprag --profile server serve --host 127.0.0.1 --port 8080
```

En navegación **no** hace falta internet si los pesos ya están en `models/`.

---

## Instalar LLM en Windows (`llama-cpp-python`)

`pip install -e ".[llm]"` en Windows suele **fallar**: descarga el `.tar.gz` fuente y Windows corta rutas largas en `%TEMP%` (`OSError: No such file or directory` con paths de `vendor/llama.cpp/...`).

Instala un **wheel precompilado** (no compiles desde fuente):

```powershell
# CPU (vale para probar GGUF sin CUDA en llama-cpp)
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# O con CUDA (elige la que coincida con tu toolkit; ejemplo 12.1):
# pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

Luego, si quieres el extra del proyecto sin recompilar:

```powershell
pip install -e ".[llm]" --no-deps
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

Alternativas de índice CUDA: `cu118`, `cu121`, `cu122`, `cu123`, `cu124`, `cu125`, `cu130`, `cu132`.

Con `balanced` **no necesitas** este paso.

---

## Resumen de comandos por perfil

```bash
# --- LITE ---
shiprag --profile lite ingest data/sample
shiprag --profile lite serve --port 8080

# --- HOME ---
python scripts/download_models.py --profile home
shiprag --profile home ingest data/sample
shiprag --profile home serve --port 8080

# --- BALANCED (recomendado 8–12 GB VRAM) ---
python scripts/download_models.py --profile balanced   # o reutiliza pesos de workstation
shiprag --profile balanced ingest data/sample
shiprag --profile balanced serve --port 8080

# --- WORKSTATION (GPU + LLM opcional) ---
python scripts/download_models.py --profile workstation
# (opcional) GGUF + wheel llama-cpp + llm.enabled: true
shiprag --profile workstation ingest data/sample
shiprag --profile workstation serve --port 8080

# --- SERVER ---
python scripts/download_models.py --profile server
shiprag --profile server ingest data/sample
shiprag --profile server serve --port 8080
```

---

## Notas útiles

- **Re-ingesta al cambiar de perfil**: los índices no son intercambiables (`data/indexes/lite` ≠ `home` ≠ `balanced` ≠ …).
- **Emergencias**: en todos los perfiles el modo emergencia es extractivo (citas del manual), aunque haya LLM.
- **Docker (lite)**: `docker compose up --build` — ver también [docs/OPENWEBUI.md](docs/OPENWEBUI.md).
- **Guía corta casa**: [QUICKSTART_CASA.md](QUICKSTART_CASA.md).
