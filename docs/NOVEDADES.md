# Novedades ShipRAG — router, verifier NLI, zona DP y tiers de modelos

Documento de referencia de las mejoras recientes respecto al MVP inicial.
Complementa [ARCHITECTURE.md](ARCHITECTURE.md) y [DOCUMENTACION.md](DOCUMENTACION.md).

**Fecha:** 2026-08-10

---

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
