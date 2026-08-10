# ShipRAG — arranque en PC de casa (5 minutos)

Perfil por defecto: **`lite`** (sin GPU, sin modelos pesados).

## 1. Requisitos

- Python 3.11+ (3.12 ideal)
- ~1 GB libre (con `.venv`)
- No hace falta internet **después** de `pip install`

## 2. Instalar

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```powershell
# Windows (PowerShell)
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

O usa los scripts:

- Linux/macOS: `bash scripts/start_lite.sh`
- Windows: `scripts\start_lite.bat`

## 3. Probar

```bash
shiprag --profile lite ingest data/sample
shiprag --profile lite query "procedimiento hombre al agua" --emergency
shiprag --profile lite serve --port 8080
```

Abre: http://127.0.0.1:8080

Smoke test automático:

```bash
shiprag --profile lite smoke
```

## 4. Preguntas de prueba sugeridas

| Pregunta | Esperado |
|---|---|
| procedimiento hombre al agua | zona emergencias, citas MOB, pasos puente |
| alarma FO-12 del generador | zona maquinaria, FO-12 / combustible |
| derrame SOPEP | emergencias, contención + notificación |
| watch circle en DP | zona `posicionamiento_dinamico` (sin corpus aún puede abstenerse) |
| blackout eléctrico | emergencias (override por patrón `blackout`) |
| receta de paella | abstención / no encontrado |

## 5. Subir de nivel (opcional)

```bash
# Casa CPU
python scripts/download_models.py --profile home
shiprag --profile home ingest data/sample
shiprag --profile home serve

# Prototipo GPU (~12 GB) — distinto del stack a bordo
python scripts/download_models.py --profile workstation
shiprag --profile workstation serve
```

Más detalle: [docs/NOVEDADES.md](docs/NOVEDADES.md) · [PERFILES.md](PERFILES.md)

## 6. Empaquetar / copiar

```bash
python scripts/pack_for_home.py
# → dist/shiprag-home-kit.zip
```

Copia ese zip al PC, descomprime e instala como arriba.
