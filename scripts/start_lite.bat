@echo off
REM Arranque rapido perfil lite (PC casa - Windows)
cd /d "%~dp0\.."

if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -U pip
pip install -q -e ".[dev]"

set SHIPRAG_PROFILE=lite
echo ==^> Ingesta sample (lite)
shiprag --profile lite ingest data/sample
echo ==^> Smoke
shiprag --profile lite smoke
echo ==^> UI en http://127.0.0.1:8080
shiprag --profile lite serve --host 127.0.0.1 --port 8080
pause
