.PHONY: install ingest serve test eval models profiles runtime smoke pack pdfs doctor docker

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -e ".[dev]"

ingest:
	. .venv/bin/activate && shiprag --profile lite ingest data/sample

serve:
	. .venv/bin/activate && shiprag --profile lite serve --host 127.0.0.1 --port 8080

serve-home:
	. .venv/bin/activate && shiprag --profile home serve --host 127.0.0.1 --port 8080

test:
	. .venv/bin/activate && pytest -q

eval:
	. .venv/bin/activate && shiprag --profile lite eval eval/golden_set.jsonl

smoke:
	. .venv/bin/activate && shiprag --profile lite smoke

doctor:
	. .venv/bin/activate && shiprag --profile lite doctor

profiles:
	. .venv/bin/activate && shiprag profiles

runtime:
	. .venv/bin/activate && shiprag --profile lite runtime

models-home:
	. .venv/bin/activate && python scripts/download_models.py --profile home

models-server:
	. .venv/bin/activate && python scripts/download_models.py --profile server

pdfs:
	. .venv/bin/activate && python scripts/make_sample_pdfs.py

pack:
	. .venv/bin/activate && python scripts/pack_for_home.py

docker:
	docker compose up --build

docker-openwebui:
	docker compose --profile openwebui up --build
