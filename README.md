# ShipRAG — RAG offline para documentación de buque

Sistema RAG **100% offline** orientado a consultas de alta precisión sobre manuales,
procedimientos de emergencia, planos, listas de chequeo y documentación técnica naval.

**Documentación completa:** [docs/DOCUMENTACION.md](docs/DOCUMENTACION.md)  
**Novedades (router embeddings, NLI, zona DP, tiers):** [docs/NOVEDADES.md](docs/NOVEDADES.md)  
(cómo está montado, inventario, uso, qué falta y próximos pasos)

**Prioridad de diseño:** reducir alucinaciones y abstenerse cuando no haya evidencia,
no maximizar “fluidez” de respuesta.

---

## 1. Por qué esta arquitectura (y qué descartamos)

| Decisión | Alternativa rechazada | Motivo |
|---|---|---|
| **Extractivo-first** en emergencias | Solo LLM generativo | Un paso inventado en un SOPEP/ISM puede matar gente. Preferimos citar el procedimiento oficial. |
| **Híbrido BM25 + denso + RRF** | Solo vectores | Consultas con códigos (`FO-12`, `P-255`, `SOLAS III/20`) fallan en embedding puro. |
| **Reranker cross-encoder local** | Top-k vectorial directo | Mejora precisión@k en dominios técnicos con vocabulario denso. |
| **Mini-expertos por zona** (índices separados) | Un solo índice global | Reduce contaminación entre dominios (puente ≠ máquina) y permite reglas distintas. |
| **Verificación post-generación** | Confiar en el LLM | Grounding léxico + NLI opcional + umbral + abstención. |
| **Chroma local + BM25 en disco** | Qdrant/Elastic/Weaviate server | Menos moving parts a bordo; sin daemons externos. |
| **Modelos locales plugables** | APIs cloud | Sin internet en operación. |

---

## 2. Arquitectura

```
                    ┌─────────────────────────────────────────┐
                    │              UI / API local              │
                    │   (FastAPI + UI estática / CLI)          │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │         Orquestador central             │
                    │  · clasifica criticidad / intención     │
                    │  · router zona (embeddings / keywords)  │
                    │  · emergencia → fuerza emergencias      │
                    │  · modo: normal | emergencia | citas    │
                    └──────────────────┬──────────────────────┘
                                       │
          ┌────────────────────────────┼────────────────────────────┐
          ▼                            ▼                            ▼
   ┌──────────────┐           ┌──────────────┐            ┌──────────────┐
   │ Experto      │           │ Experto      │    ...     │ Experto DP   │
   │ PUENTE       │           │ MAQUINARIA   │            │ / EMERGENCIAS│
   │ idx+reglas   │           │ idx+reglas   │            │ idx+reglas   │
   └──────┬───────┘           └──────┬───────┘            └──────┬───────┘
          │                          │                           │
          └────────────┬─────────────┴─────────────┬─────────────┘
                       ▼                           ▼
              Recuperación híbrida          Reranking local
              (BM25 ⊕ densos → RRF)         (cross-encoder)
                       │                           │
                       └─────────────┬─────────────┘
                                     ▼
                          Generación controlada
                     · extractiva (emergencias)
                     · semi-extractiva (normal)
                     · LLM local opcional
                                     ▼
                          Verificador de grounding
                     · overlap léxico
                     · NLI entailment (opcional)
                     · umbral → ABSTENERSE
                                     ▼
                          Respuesta + citas + trace
```

### Flujo de ingesta

1. PDF / imagen → extracción de texto (PyMuPDF) + tablas (pdfplumber).
2. Si página sin texto útil → OCR local (Tesseract, opcional).
3. Detección de estructura: encabezados, secciones, listas numeradas, avisos.
4. Chunking **estructural** (sección/procedimiento), no solo sliding window.
5. Metadatos: `doc_id`, versión, página, sección, zona, tipo, idioma, criticidad.
6. Indexado dual: embedding denso (Chroma) + BM25 lexical por zona.

### Flujo de consulta

1. Router → zona(s) + criticidad + modo (override duro si emergencia).
2. Filtros metadata (zona, tipo, idioma, criticidad).
3. Retrieve híbrido (top N lexical + top N vectorial) → RRF.
4. Rerank local → top K.
5. Generación según modo (extractivo si emergencia / confianza baja).
6. Verificación léxica (+ NLI si hay modelo): grounding bajo → abstención.
7. Trace de recuperación para auditoría.

---

## 3. Zonas / mini-expertos

| Zona | Dominio |
|---|---|
| `puente` | Navegación, bridge, COLREG, ECDIS |
| `maquinaria` | Sala de máquinas, motores, propulsión |
| `cubierta` | Cubierta, carga, amarre |
| `seguridad` | ISM, LSA, fire, drills |
| `emergencias` | SOPEP, abandono, hombre al agua, incendio |
| `electricidad` | Cuadros, blackout, UPS |
| `comunicaciones` | GMDSS, VHF, distress |
| `posicionamiento_dinamico` | DP, thrusters, watch circle, PRS/DGPS |
| `general` | Fallback / multi-dominio |

Cada zona tiene colección propia + reglas (p.ej. emergencias → modo extractivo por defecto).

---

## 4. Stack offline

| Capa | Tecnología MVP | Notas |
|---|---|---|
| API | FastAPI | Local `127.0.0.1` |
| Vectores | ChromaDB (persistente) | Sin servidor externo |
| Lexical | BM25 (`rank_bm25`) serializado | Códigos y términos exactos |
| Embeddings | `sentence-transformers` (E5 / BGE-M3) | Descarga **en puerto**; runtime offline |
| Reranker | Cross-encoder local | Fallback lexical si no hay modelo |
| Verifier | Léxico + NLI opcional (mDeBERTa) | Lite = solo léxico |
| LLM | Opcional `llama-cpp-python` + GGUF | No obligatorio: modo extractivo funciona solo |
| PDF | PyMuPDF + pdfplumber | Texto + tablas |
| OCR | Tesseract (opcional) | Planos/escaneados |
| UI | HTML/JS servida por FastAPI | Sin dependencias cloud |

> **Importante:** los pesos de modelos se descargan **antes** de zarpar y se guardan en `models/`.
> En navegación el sistema no llama a internet.

---

## 5. Reducción de alucinaciones (capas)

1. **Recuperación filtrada** por zona/tipo/criticidad.
2. **Reranking** para subir evidencia real.
3. **Modo extractivo** en críticas: peega pasos del documento, no “reescribe”.
4. **Grounding check**: proporción de n-gramas de la respuesta presentes en evidencias.
5. **Umbral de confianza** compuesto (retrieval score × grounding × criticidad).
6. **Abstención explícita** si falla el umbral.
7. **Detección de conflictos** entre documentos (mismo tema, pasos distintos).
8. **Trace log** de por qué se eligió cada fragmento.

---

## 6. Uso rápido (PC de casa primero)

> **Cómo lanzar cada perfil:** **[PERFILES.md](PERFILES.md)**  
> **Novedades recientes:** **[docs/NOVEDADES.md](docs/NOVEDADES.md)**  
> Guía corta: **[QUICKSTART_CASA.md](QUICKSTART_CASA.md)** · Descarga al PC: **[DESCARGA_PC.md](DESCARGA_PC.md)** · Open WebUI: **[docs/OPENWEBUI.md](docs/OPENWEBUI.md)**  
> Arranque: `bash scripts/start_lite.sh` · Windows: `scripts\start_lite.bat` · Docker: `docker compose up --build` · Con Open WebUI: `docker compose --profile openwebui up --build`

ShipRAG tiene **perfiles de hardware** para no obligarte a montar modelos de servidor:

| Perfil | Para qué | Modelos |
|---|---|---|
| `lite` (**default**) | Probar flujo sin pesos | Ninguno (BM25 + hash + extractivo + keywords) |
| `home` | Casa con CPU | e5-small + MiniLM + NLI opcional |
| `balanced` | **GPU 8–12 GB sin apretar** | e5-base + bge-reranker (+ NLI), sin LLM pesado |
| `workstation` | Prototipo / banco de pruebas (~12 GB) | e5-base + bge-reranker-base + Qwen2.5-7B |
| `server` | **Producción a bordo** | BGE-M3 + bge-reranker-v2-m3 + Qwen3-32B (fallback a stack WS) |

```bash
# Instalación
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# --- PC de casa (recomendado para empezar) ---
shiprag --profile lite ingest data/sample
shiprag --profile lite query "procedimiento hombre al agua" --emergency
shiprag --profile lite query "watch circle en DP"
shiprag --profile lite serve --port 8080
# Abrir http://127.0.0.1:8080

# Ver perfil activo
shiprag --profile lite runtime
shiprag profiles

# --- Más adelante, con modelos pequeños locales ---
python scripts/download_models.py --profile home
shiprag --profile home ingest data/sample
shiprag --profile home serve

# --- Prototipo GPU casa ---
python scripts/download_models.py --profile workstation
shiprag --profile workstation serve

# --- Producción a bordo ---
python scripts/download_models.py --profile server
shiprag --profile server serve
# LLM: models/llm/qwen3-32b-instruct-q4_k_m.gguf (fallback Qwen2.5-7B)

# Tests + evaluación
pytest -q
shiprag --profile lite eval eval/golden_set.jsonl
# Borrador preguntas tripulación (revisar antes de fusionar):
# shiprag --profile lite eval eval/golden_set_crew_draft.jsonl
```

También puedes fijar el perfil por entorno: `export SHIPRAG_PROFILE=lite`.

Cada perfil usa su propio `data/indexes/<perfil>/` para no mezclar embeddings distintos.

---

## 7. Estructura del repo

```
src/shiprag/
  core/           # schemas, config, logging
  ingest/         # PDF, OCR, chunking estructural
  index/          # Chroma + BM25
  retrieval/      # híbrido, filtros, rerank
  experts/        # router y mini-expertos por zona
  generation/     # extractivo, LLM opcional, verificador
  orchestration/  # pipeline end-to-end
  api/            # FastAPI
  ui/static/      # interfaz local
  cli.py
config/default.yaml
data/sample/      # documentos sintéticos de demo
tests/            # unitarios
eval/             # banco de evaluación + métricas
```

---

## 8. Limitaciones honestas del MVP

1. **OCR de planos complejos** (P&ID densos) sigue siendo frágil; para producción hay que
   anotar planos con capas de texto o metadatos CAD.
2. **Tablas multi-página** se fragmentan; la calidad depende del PDF de origen.
3. **El LLM local** no viene empaquetado (pesos grandes); el sistema es usable en modo
   extractivo/semi-extractivo sin él — y eso es **preferible** en emergencias.
4. **El router por embeddings** mejora el lexical, pero los `examples` por zona deben
   afinarse con consultas reales de la tripulación; sin corpus DP el experto DP recupera poco.
5. **Diagramas/figuras** se indexan por caption + OCR de página; no hay VLM offline
   por defecto (añadible después con un modelo vision local cuantizado).

---

## 9. Roadmap post-MVP

- Afinar `examples`/keywords del router con logs de consultas reales.
- Versionado documental con invalidación de índice.
- Firma/integridad de manuales (hash + allowlist).
- UI de revisión humana (HITL) para validar citas críticas.
- Export de traces a bitácora ISM.
- Soporte de paquetes de modelos “air-gap” (tar.gz con embeddings+reranker+NLI+LLM).
- Fusionar `golden_set_crew_draft.jsonl` → golden oficial tras revisión de tripulación.
---

## Licencia

MIT — adaptar a políticas internas del armador antes de despliegue a bordo.
