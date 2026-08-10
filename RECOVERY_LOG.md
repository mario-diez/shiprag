# ShipRAG recovery log

Recovered from cloud-agent transcript by replaying applied `search_replace`/`editFileResult` unified diffs in chronological order (with multi-hunk offset tracking).

## Final summary

- Transcript: `/tmp/cursor/cloud-agent-transcripts/2026-08-10T07-06-24Z-7179/bc-019fd85c-33c7-7fd0-b67c-ef423c4ba2a3/transcript.json`
- Applied diff ops: 142 (0 failed)
- Skipped empty/failed tool results: 7
- Source/docs/config files from diffs: 64
- Empty package stubs recreated (`touch` equivalents): 9 `__init__.py` + `models/.gitkeep`
- Sample PDFs regenerated via `scripts/make_sample_pdfs.py`: 8
- Total files on disk (excl. recovery log / .git): 82
- AST parse of all `.py` files: 37 OK / 0 errors
- Could not reconstruct: runtime artifacts intentionally skipped (`.venv`, `data/indexes`, `data/raw`, `data/logs`, `dist`, egg-info). Sample PDFs were not in diffs (binary); regenerated from TXT.

---

## Failures
(none)

## Files
- `.gitignore`
- `DESCARGA_PC.md`
- `Dockerfile`
- `LICENSE`
- `Makefile`
- `QUICKSTART_CASA.md`
- `README.md`
- `config/default.yaml`
- `config/profiles/home.yaml`
- `config/profiles/lite.yaml`
- `config/profiles/server.yaml`
- `config/profiles/workstation.yaml`
- `data/sample/comunicaciones_gmdss_distress.txt`
- `data/sample/cubierta_amarre.meta.yaml`
- `data/sample/cubierta_amarre.txt`
- `data/sample/electricidad_blackout.txt`
- `data/sample/emergencias_hombre_al_agua.txt`
- `data/sample/emergencias_sopep.txt`
- `data/sample/maquinaria_generador.txt`
- `data/sample/puente_checklist_guardia.txt`
- `data/sample/seguridad_incendio_maquinas.meta.yaml`
- `data/sample/seguridad_incendio_maquinas.txt`
- `docker-compose.yml`
- `docs/ARCHITECTURE.md`
- `docs/DOCUMENTACION.md`
- `docs/MODELS_WORKSTATION.md`
- `docs/OPENWEBUI.md`
- `eval/golden_set.jsonl`
- `pyproject.toml`
- `requirements.txt`
- `scripts/download_models.py`
- `scripts/make_sample_pdfs.py`
- `scripts/pack_for_home.py`
- `scripts/start_lite.bat`
- `scripts/start_lite.ps1`
- `scripts/start_lite.sh`
- `src/shiprag/__init__.py`
- `src/shiprag/api/app.py`
- `src/shiprag/api/openai_compat.py`
- `src/shiprag/cli.py`
- `src/shiprag/core/config.py`
- `src/shiprag/core/logging.py`
- `src/shiprag/core/schemas.py`
- `src/shiprag/doctor.py`
- `src/shiprag/eval_runner.py`
- `src/shiprag/experts/expert.py`
- `src/shiprag/experts/router.py`
- `src/shiprag/generation/generator.py`
- `src/shiprag/generation/verifier.py`
- `src/shiprag/index/embeddings.py`
- `src/shiprag/index/lexical.py`
- `src/shiprag/index/store.py`
- `src/shiprag/ingest/chunker.py`
- `src/shiprag/ingest/ocr.py`
- `src/shiprag/ingest/pdf_extractor.py`
- `src/shiprag/ingest/pipeline.py`
- `src/shiprag/orchestration/pipeline.py`
- `src/shiprag/retrieval/hybrid.py`
- `src/shiprag/retrieval/reranker.py`
- `src/shiprag/ui/static/app.js`
- `src/shiprag/ui/static/index.html`
- `src/shiprag/ui/static/styles.css`
- `tests/test_core.py`
- `tests/test_openai_compat.py`
- `src/shiprag/ingest/__init__.py`
- `src/shiprag/index/__init__.py`
- `src/shiprag/retrieval/__init__.py`
- `src/shiprag/experts/__init__.py`
- `src/shiprag/generation/__init__.py`
- `src/shiprag/orchestration/__init__.py`
- `src/shiprag/api/__init__.py`
- `src/shiprag/core/__init__.py`
- `src/shiprag/ui/__init__.py`
- `models/.gitkeep`

## Op log
OK pyproject.toml lines=52 hunks=1 [turn-0:step:14:tool]
OK README.md lines=218 hunks=1 [turn-0:step:16:tool]
OK config/default.yaml lines=105 hunks=1 [turn-0:step:17:tool]
OK src/shiprag/__init__.py lines=3 hunks=1 [turn-0:step:19:tool]
OK src/shiprag/core/config.py lines=178 hunks=1 [turn-0:step:20:tool]
OK src/shiprag/core/schemas.py lines=197 hunks=1 [turn-0:step:21:tool]
OK src/shiprag/core/logging.py lines=38 hunks=1 [turn-0:step:22:tool]
OK src/shiprag/ingest/ocr.py lines=50 hunks=1 [turn-0:step:24:tool]
OK src/shiprag/ingest/pdf_extractor.py lines=225 hunks=1 [turn-0:step:25:tool]
OK src/shiprag/ingest/chunker.py lines=162 hunks=1 [turn-0:step:26:tool]
OK src/shiprag/ingest/pipeline.py lines=179 hunks=1 [turn-0:step:28:tool]
OK src/shiprag/index/embeddings.py lines=150 hunks=1 [turn-0:step:29:tool]
OK src/shiprag/index/lexical.py lines=120 hunks=1 [turn-0:step:30:tool]
OK src/shiprag/index/store.py lines=228 hunks=1 [turn-0:step:32:tool]
OK src/shiprag/retrieval/hybrid.py lines=158 hunks=1 [turn-0:step:33:tool]
OK src/shiprag/retrieval/reranker.py lines=115 hunks=1 [turn-0:step:34:tool]
OK src/shiprag/experts/router.py lines=146 hunks=1 [turn-0:step:36:tool]
OK src/shiprag/experts/expert.py lines=62 hunks=1 [turn-0:step:37:tool]
OK src/shiprag/generation/generator.py lines=261 hunks=1 [turn-0:step:38:tool]
OK src/shiprag/generation/verifier.py lines=161 hunks=1 [turn-0:step:39:tool]
OK src/shiprag/orchestration/pipeline.py lines=151 hunks=1 [turn-0:step:41:tool]
OK src/shiprag/cli.py lines=124 hunks=1 [turn-0:step:42:tool]
OK src/shiprag/api/app.py lines=141 hunks=1 [turn-0:step:44:tool]
OK src/shiprag/ui/static/index.html lines=92 hunks=1 [turn-0:step:45:tool]
OK src/shiprag/ui/static/styles.css lines=176 hunks=1 [turn-0:step:46:tool]
OK src/shiprag/ui/static/app.js lines=115 hunks=1 [turn-0:step:47:tool]
OK data/sample/emergencias_hombre_al_agua.txt lines=39 hunks=1 [turn-0:step:50:tool]
OK data/sample/emergencias_sopep.txt lines=31 hunks=1 [turn-0:step:51:tool]
OK data/sample/maquinaria_generador.txt lines=32 hunks=1 [turn-0:step:52:tool]
OK data/sample/puente_checklist_guardia.txt lines=23 hunks=1 [turn-0:step:53:tool]
OK data/sample/electricidad_blackout.txt lines=26 hunks=1 [turn-0:step:54:tool]
OK data/sample/comunicaciones_gmdss_distress.txt lines=28 hunks=1 [turn-0:step:55:tool]
OK eval/golden_set.jsonl lines=10 hunks=1 [turn-0:step:57:tool]
OK src/shiprag/eval_runner.py lines=132 hunks=1 [turn-0:step:58:tool]
OK tests/test_core.py lines=153 hunks=1 [turn-0:step:59:tool]
OK .gitignore lines=18 hunks=1 [turn-0:step:60:tool]
OK docs/ARCHITECTURE.md lines=56 hunks=1 [turn-0:step:62:tool]
OK src/shiprag/generation/verifier.py lines=160 hunks=1 [turn-0:step:65:tool]
OK scripts/download_models.py lines=64 hunks=1 [turn-0:step:66:tool]
OK requirements.txt lines=22 hunks=1 [turn-0:step:67:tool]
OK tests/test_core.py lines=151 hunks=1 [turn-0:step:68:tool]
OK src/shiprag/generation/verifier.py lines=171 hunks=10 [turn-0:step:79:tool]
OK src/shiprag/generation/generator.py lines=273 hunks=3 [turn-0:step:80:tool]
OK src/shiprag/core/schemas.py lines=201 hunks=1 [turn-0:step:81:tool]
OK src/shiprag/core/schemas.py lines=201 hunks=1 [turn-0:step:82:tool]
OK src/shiprag/ingest/pdf_extractor.py lines=226 hunks=1 [turn-0:step:87:tool]
OK src/shiprag/ingest/pdf_extractor.py lines=228 hunks=1 [turn-0:step:88:tool]
OK src/shiprag/ingest/pipeline.py lines=246 hunks=1 [turn-0:step:89:tool]
OK src/shiprag/generation/generator.py lines=274 hunks=2 [turn-0:step:90:tool]
OK src/shiprag/ingest/pipeline.py lines=234 hunks=1 [turn-0:step:96:tool]
OK src/shiprag/ingest/pipeline.py lines=234 hunks=1 [turn-0:step:97:tool]
OK config/default.yaml lines=105 hunks=3 [turn-0:step:107:tool]
OK src/shiprag/experts/router.py lines=158 hunks=1 [turn-0:step:108:tool]
OK src/shiprag/retrieval/reranker.py lines=121 hunks=3 [turn-0:step:109:tool]
OK src/shiprag/ingest/chunker.py lines=163 hunks=1 [turn-0:step:110:tool]
SKIP turn-0:step:111:tool
OK eval/golden_set.jsonl lines=10 hunks=1 [turn-0:step:113:tool]
OK src/shiprag/orchestration/pipeline.py lines=158 hunks=1 [turn-0:step:114:tool]
OK src/shiprag/orchestration/pipeline.py lines=158 hunks=1 [turn-0:step:115:tool]
OK src/shiprag/orchestration/pipeline.py lines=158 hunks=1 [turn-0:step:116:tool]
OK src/shiprag/generation/verifier.py lines=177 hunks=1 [turn-0:step:118:tool]
OK src/shiprag/generation/generator.py lines=283 hunks=1 [turn-0:step:119:tool]
OK LICENSE lines=21 hunks=1 [turn-0:step:123:tool]
OK Makefile lines=20 hunks=1 [turn-0:step:124:tool]
OK src/shiprag/api/app.py lines=139 hunks=1 [turn-0:step:127:tool]
OK src/shiprag/orchestration/pipeline.py lines=158 hunks=1 [turn-0:step:131:tool]
OK config/profiles/lite.yaml lines=42 hunks=1 [turn-4:step:12:tool]
OK config/profiles/home.yaml lines=47 hunks=1 [turn-4:step:13:tool]
OK config/profiles/server.yaml lines=49 hunks=1 [turn-4:step:14:tool]
OK config/default.yaml lines=110 hunks=4 [turn-4:step:15:tool]
OK src/shiprag/core/config.py lines=282 hunks=6 [turn-4:step:17:tool]
OK src/shiprag/index/embeddings.py lines=147 hunks=2 [turn-4:step:18:tool]
OK src/shiprag/retrieval/reranker.py lines=132 hunks=1 [turn-4:step:19:tool]
OK src/shiprag/cli.py lines=162 hunks=6 [turn-4:step:21:tool]
OK src/shiprag/api/app.py lines=143 hunks=1 [turn-4:step:24:tool]
OK scripts/download_models.py lines=77 hunks=3 [turn-4:step:25:tool]
OK Makefile lines=33 hunks=1 [turn-4:step:26:tool]
OK README.md lines=233 hunks=1 [turn-4:step:28:tool]
OK src/shiprag/ui/static/app.js lines=117 hunks=1 [turn-4:step:29:tool]
OK tests/test_core.py lines=178 hunks=1 [turn-4:step:30:tool]
OK tests/test_core.py lines=180 hunks=1 [turn-4:step:35:tool]
OK docs/ARCHITECTURE.md lines=68 hunks=1 [turn-4:step:38:tool]
OK QUICKSTART_CASA.md lines=73 hunks=1 [turn-6:step:6:tool]
OK scripts/start_lite.sh lines=20 hunks=1 [turn-6:step:7:tool]
OK scripts/start_lite.bat lines=19 hunks=1 [turn-6:step:8:tool]
OK scripts/start_lite.ps1 lines=18 hunks=1 [turn-6:step:9:tool]
OK scripts/make_sample_pdfs.py lines=41 hunks=1 [turn-6:step:10:tool]
OK scripts/pack_for_home.py lines=65 hunks=1 [turn-6:step:11:tool]
OK src/shiprag/cli.py lines=213 hunks=2 [turn-6:step:13:tool]
OK src/shiprag/ui/static/index.html lines=106 hunks=3 [turn-6:step:14:tool]
OK src/shiprag/ui/static/styles.css lines=192 hunks=1 [turn-6:step:15:tool]
OK src/shiprag/ui/static/app.js lines=136 hunks=2 [turn-6:step:16:tool]
SKIP turn-6:step:19:tool
OK Makefile lines=41 hunks=3 [turn-6:step:21:tool]
OK src/shiprag/cli.py lines=213 hunks=1 [turn-6:step:22:tool]
OK README.md lines=236 hunks=1 [turn-6:step:23:tool]
OK .gitignore lines=2 hunks=1 [turn-6:step:24:tool]
OK .gitignore lines=19 hunks=1 [turn-6:step:34:tool]
OK src/shiprag/doctor.py lines=121 hunks=1 [turn-7:step:6:tool]
OK Dockerfile lines=29 hunks=1 [turn-7:step:7:tool]
OK docker-compose.yml lines=24 hunks=1 [turn-7:step:8:tool]
OK data/sample/seguridad_incendio_maquinas.txt lines=29 hunks=1 [turn-7:step:9:tool]
OK data/sample/cubierta_amarre.txt lines=24 hunks=1 [turn-7:step:10:tool]
OK DESCARGA_PC.md lines=52 hunks=1 [turn-7:step:11:tool]
OK src/shiprag/ingest/pipeline.py lines=246 hunks=1 [turn-7:step:13:tool]
OK src/shiprag/ingest/pipeline.py lines=280 hunks=1 [turn-7:step:14:tool]
OK data/sample/seguridad_incendio_maquinas.meta.yaml lines=10 hunks=1 [turn-7:step:15:tool]
SKIP turn-7:step:16:tool
OK src/shiprag/cli.py lines=222 hunks=1 [turn-7:step:17:tool]
OK src/shiprag/cli.py lines=223 hunks=1 [turn-7:step:18:tool]
SKIP turn-7:step:19:tool
OK eval/golden_set.jsonl lines=12 hunks=1 [turn-7:step:21:tool]
OK config/default.yaml lines=110 hunks=1 [turn-7:step:23:tool]
OK config/default.yaml lines=110 hunks=1 [turn-7:step:24:tool]
OK data/sample/seguridad_incendio_maquinas.meta.yaml lines=9 hunks=1 [turn-7:step:26:tool]
OK data/sample/cubierta_amarre.meta.yaml lines=9 hunks=1 [turn-7:step:27:tool]
OK eval/golden_set.jsonl lines=12 hunks=1 [turn-7:step:28:tool]
OK tests/test_core.py lines=208 hunks=1 [turn-7:step:29:tool]
OK README.md lines=236 hunks=1 [turn-7:step:30:tool]
SKIP turn-7:step:31:tool
OK Makefile lines=47 hunks=3 [turn-7:step:33:tool]
OK eval/golden_set.jsonl lines=12 hunks=1 [turn-7:step:36:tool]
OK src/shiprag/generation/generator.py lines=291 hunks=1 [turn-7:step:40:tool]
OK src/shiprag/retrieval/reranker.py lines=136 hunks=1 [turn-7:step:41:tool]
OK eval/golden_set.jsonl lines=12 hunks=1 [turn-7:step:46:tool]
OK docs/DOCUMENTACION.md lines=637 hunks=1 [turn-8:step:4:tool]
SKIP turn-8:step:6:tool
OK docs/ARCHITECTURE.md lines=71 hunks=1 [turn-8:step:7:tool]
OK README.md lines=239 hunks=1 [turn-8:step:13:tool]
OK src/shiprag/api/openai_compat.py lines=238 hunks=1 [turn-12:step:6:tool]
OK src/shiprag/api/app.py lines=147 hunks=2 [turn-12:step:7:tool]
OK docker-compose.yml lines=60 hunks=2 [turn-12:step:8:tool]
OK docs/OPENWEBUI.md lines=87 hunks=1 [turn-12:step:9:tool]
OK tests/test_openai_compat.py lines=70 hunks=1 [turn-12:step:13:tool]
OK Makefile lines=50 hunks=1 [turn-12:step:14:tool]
OK README.md lines=239 hunks=1 [turn-12:step:15:tool]
OK src/shiprag/api/app.py lines=152 hunks=1 [turn-12:step:16:tool]
OK tests/test_openai_compat.py lines=59 hunks=3 [turn-12:step:19:tool]
OK src/shiprag/index/store.py lines=237 hunks=1 [turn-12:step:20:tool]
OK docs/DOCUMENTACION.md lines=638 hunks=1 [turn-12:step:23:tool]
OK config/profiles/workstation.yaml lines=57 hunks=1 [turn-17:step:2:tool]
OK src/shiprag/core/config.py lines=282 hunks=1 [turn-17:step:3:tool]
OK src/shiprag/cli.py lines=223 hunks=1 [turn-17:step:4:tool]
OK scripts/download_models.py lines=82 hunks=1 [turn-17:step:5:tool]
OK scripts/download_models.py lines=82 hunks=1 [turn-17:step:6:tool]
OK docs/MODELS_WORKSTATION.md lines=89 hunks=1 [turn-17:step:7:tool]
OK src/shiprag/orchestration/pipeline.py lines=169 hunks=2 [turn-17:step:12:tool]
SKIP turn-17:step:13:tool
OK README.md lines=240 hunks=1 [turn-17:step:16:tool]

## Git
- Branch: `cursor/shiprag-offline-mvp-a2a3`
- Commit: `53e25e8` succeeded locally
- Push: skipped — no `origin` remote configured in this recovered workspace (`repoUrl` null)
