"""Subidas seguras, token de administración y borrado vía API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shiprag.api.app import create_app, safe_upload_name
from shiprag.core.config import AppConfig, PathsConfig, clear_config_cache, load_config

DOC = b"""# Procedimiento de purga
# Zona: maquinaria

## 1. Pasos
1. Abrir la valvula de purga del calderin ALFA-7.
"""


@pytest.fixture()
def cfg(tmp_path: Path) -> AppConfig:
    clear_config_cache()
    base = load_config(profile="lite")
    data = tmp_path / "data"
    out = base.model_copy(deep=True)
    out.paths = PathsConfig(
        data_dir=str(data),
        raw_dir=str(data / "raw"),
        index_dir=str(data / "indexes"),
        log_dir=str(data / "logs"),
        models_dir=str(tmp_path / "models"),
        sample_dir=str(Path(__file__).resolve().parents[1] / "data" / "sample"),
    )
    out.api.admin_token_env = "SHIPRAG_TEST_ADMIN_TOKEN"
    return out


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../evil.txt", "evil.txt"),
        ("..\\..\\windows\\evil.pdf", "evil.pdf"),
        ("/etc/passwd", "passwd"),
        ("manual de máquinas.pdf", "manual de máquinas.pdf"),
        ("..", "upload.bin"),
        ("CON.txt", "_CON.txt"),
        ("a<b>|c?.md", "a_b_c_.md"),
    ],
)
def test_safe_upload_name(raw: str, expected: str):
    assert safe_upload_name(raw) == expected


def test_upload_cannot_escape_uploads_dir(cfg: AppConfig, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SHIPRAG_TEST_ADMIN_TOKEN", raising=False)
    client = TestClient(create_app(cfg))
    r = client.post(
        "/api/ingest",
        files={"file": ("../../../escape.txt", DOC, "text/plain")},
    )
    assert r.status_code == 200, r.text
    uploads = cfg.raw_path / "uploads"
    assert (uploads / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not (cfg.raw_path.parent / "escape.txt").exists()


def test_rejects_unsupported_type_and_oversize(cfg: AppConfig, monkeypatch):
    monkeypatch.delenv("SHIPRAG_TEST_ADMIN_TOKEN", raising=False)
    cfg.api.max_upload_mb = 1
    client = TestClient(create_app(cfg))
    r = client.post("/api/ingest", files={"file": ("macro.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415
    big = b"a" * (1024 * 1024 + 10)
    r = client.post("/api/ingest", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413
    assert not list((cfg.raw_path / "uploads").glob("*.part"))


def test_invalid_zone_is_400_not_500(cfg: AppConfig, monkeypatch):
    monkeypatch.delenv("SHIPRAG_TEST_ADMIN_TOKEN", raising=False)
    client = TestClient(create_app(cfg))
    r = client.post(
        "/api/ingest",
        files={"file": ("doc.txt", DOC, "text/plain")},
        data={"zone": "zona-inventada"},
    )
    assert r.status_code == 400


def test_admin_token_protects_writes(cfg: AppConfig, monkeypatch):
    monkeypatch.setenv("SHIPRAG_TEST_ADMIN_TOKEN", "s3cret")
    client = TestClient(create_app(cfg))
    files = {"file": ("doc.txt", DOC, "text/plain")}

    assert client.post("/api/ingest", files=files).status_code == 401
    bad = {"Authorization": "Bearer otro"}
    assert client.post("/api/ingest", files=files, headers=bad).status_code == 401

    ok = {"Authorization": "Bearer s3cret"}
    r = client.post("/api/ingest", files=files, headers=ok)
    assert r.status_code == 200, r.text
    doc_id = r.json()["doc_id"]

    # Lectura sigue abierta
    assert client.get("/api/documents").status_code == 200
    q = client.post("/api/query", json={"query": "purga calderin ALFA-7"})
    assert q.status_code == 200

    assert client.delete(f"/api/documents/{doc_id}").status_code == 401
    r = client.delete(f"/api/documents/{doc_id}", headers=ok)
    assert r.status_code == 200 and r.json()["action"] == "deleted"
    assert client.delete(f"/api/documents/{doc_id}", headers=ok).status_code == 404
    assert client.get("/api/health").json()["write_protected"] is True


def test_reupload_replaces_and_identical_is_unchanged(cfg: AppConfig, monkeypatch):
    monkeypatch.delenv("SHIPRAG_TEST_ADMIN_TOKEN", raising=False)
    client = TestClient(create_app(cfg))
    first = client.post("/api/ingest", files={"file": ("doc.txt", DOC, "text/plain")}).json()
    same = client.post("/api/ingest", files={"file": ("doc.txt", DOC, "text/plain")}).json()
    assert same["action"] == "unchanged"
    v2 = DOC.replace(b"ALFA-7", b"BETA-9")
    upd = client.post("/api/ingest", files={"file": ("doc.txt", v2, "text/plain")}).json()
    assert upd["action"] == "updated" and upd["doc_id"] == first["doc_id"]
    docs = client.get("/api/documents").json()
    assert [d["doc_id"] for d in docs] == [first["doc_id"]]
