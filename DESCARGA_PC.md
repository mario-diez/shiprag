# Cómo pasar ShipRAG del móvil / Cloud Agent al PC de casa

Agente: https://cursor.com/agents/bc-019fd85c-33c7-7fd0-b67c-ef423c4ba2a3

Repo GitHub: https://github.com/mario-diez/shiprag.git

## Opción A — Descargar desde Cursor (recomendado)

1. En el PC, abre el enlace del agente (arriba).
2. Usa la opción de **Download / Export** del workspace si aparece.
3. Si no hay export, crea un repo GitHub vacío y pide al agente (desde el PC) que lo suba, o copia los ficheros.

## Opción B — Generar zip en el agente y descargarlo

En el agente (o en el PC si ya tienes el código):

```bash
python scripts/pack_for_home.py
# → dist/shiprag-home-kit-YYYYMMDD.zip
```

Copia ese zip al PC, descomprime y sigue `QUICKSTART_CASA.md`.

## Opción C — Docker (si tienes Docker Desktop)

```bash
docker compose up --build
# http://127.0.0.1:8080
```

## Primera prueba en el PC (sin modelos pesados)

```bash
# Windows
scripts\start_lite.bat

# Linux/macOS
bash scripts/start_lite.sh
```

O manualmente:

```bash
pip install -e ".[dev]"
shiprag --profile lite doctor
shiprag --profile lite ingest data/sample
shiprag --profile lite smoke
shiprag --profile lite serve --port 8080
```

## Importante

- Perfil `lite` = suficiente para validar el flujo.
- Subir de nivel: `home` → `balanced`/`workstation` (prototipo) → `server` (producción a bordo).
- Resumen de lo nuevo: [docs/NOVEDADES.md](docs/NOVEDADES.md) · arranque: [QUICKSTART_CASA.md](QUICKSTART_CASA.md).
