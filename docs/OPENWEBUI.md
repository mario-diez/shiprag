# Open WebUI + ShipRAG

ShipRAG expone una API **compatible con OpenAI** para que puedas usar la interfaz de Open WebUI sin renunciar al pipeline anti-alucinación.

## Qué hace

```
Open WebUI (puerto 3000)
    │  POST /v1/chat/completions
    ▼
ShipRAG (puerto 8080)
    │  router (emb/keywords) → retrieve → generate → verifier (± NLI)
    ▼
Respuesta + citas (en el texto del chat)
```

El chat de Open WebUI **no** sustituye el pipeline: emergencias siguen en extractivo, y si el grounding falla ShipRAG se abstiene.

Modelos visibles en Open WebUI:

| Modelo | Comportamiento |
|---|---|
| `shiprag` | Auto (extractivo / semi según router) |
| `shiprag-emergency` | Emergencia · **solo citas** |
| `shiprag-extractive` | Extractivo |

## Opción A — Docker (recomendada)

```bash
# ShipRAG + Open WebUI
docker compose --profile openwebui up --build
```

- ShipRAG UI: http://127.0.0.1:8080  
- Open WebUI: http://127.0.0.1:3000  

La primera vez Open WebUI descarga la imagen (necesita red **una vez**). Luego puede ir offline si la imagen ya está en el PC.

## Opción B — ShipRAG local + Open WebUI en Docker

```bash
# Terminal 1
shiprag --profile lite ingest data/sample
shiprag --profile lite serve --host 0.0.0.0 --port 8080

# Terminal 2
docker run -d -p 3000:8080 \
  -e ENABLE_OLLAMA_API=false \
  -e ENABLE_OPENAI_API=true \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:8080/v1 \
  -e OPENAI_API_KEY=shiprag-local \
  -e WEBUI_AUTH=false \
  --add-host=host.docker.internal:host-gateway \
  -v openwebui_data:/app/backend/data \
  --name shiprag-openwebui \
  ghcr.io/open-webui/open-webui:main
```

En Linux a veces hace falta `host.docker.internal` como arriba.

## Opción C — Open WebUI que ya tienes instalado

1. Arranca ShipRAG: `shiprag --profile lite serve --port 8080`
2. En Open WebUI → **Admin → Connections / OpenAI**:
   - API Base URL: `http://127.0.0.1:8080/v1`
   - API Key: `shiprag-local` (cualquier string)
3. Desactiva Ollama si no lo usas, para no confundir modelos.
4. Elige el modelo `shiprag` o `shiprag-emergency` en el chat.

## Probar sin Open WebUI (smoke API)

```bash
curl -s http://127.0.0.1:8080/v1/models | python -m json.tool

curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "shiprag-emergency",
    "messages": [{"role":"user","content":"procedimiento hombre al agua"}],
    "stream": false
  }' | python -m json.tool
```

## Importante (seguridad)

- Open WebUI aquí es **solo la carcasa visual**.
- Quien decide y se abstiene sigue siendo **ShipRAG**.
- En emergencias usa el modelo `shiprag-emergency` (solo citas).
- No actives un LLM cloud detrás de Open WebUI si quieres modo air-gap.
