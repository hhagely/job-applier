from __future__ import annotations

import uvicorn
from fastapi.testclient import TestClient

from job_applier import cli
from job_applier.api.app import app
from job_applier.config import REPO_ROOT, Settings


def _clean_settings(**kwargs) -> Settings:
    """Build Settings without leaking the ambient JOB_APPLIER_* env / .env, so
    default-path assertions are deterministic even when a dev data dir is set."""
    return Settings(_env_file=None, **kwargs)


def test_config_data_dir_relocation(tmp_path):
    s = _clean_settings(data_dir=tmp_path)
    assert s.db_path == tmp_path / "jobs.db"
    assert s.resumes_dir == tmp_path / "resumes"
    # A relocated data_dir (dev copy or the packaged app's user-data dir) nests
    # applications under it, so drafts never land next to read-only install files.
    assert s.applications_dir == tmp_path / "applications"


def test_config_explicit_overrides_win(tmp_path):
    custom_db = tmp_path / "elsewhere" / "custom.db"
    custom_apps = tmp_path / "elsewhere" / "drafts"
    s = _clean_settings(data_dir=tmp_path, db_path=custom_db, applications_dir=custom_apps)
    # An explicit db_path beats the data_dir derivation; resumes_dir still derives.
    assert s.db_path == custom_db
    assert s.resumes_dir == tmp_path / "resumes"
    # An explicit applications_dir (JOB_APPLIER_APPLICATIONS_DIR) wins too.
    assert s.applications_dir == custom_apps


def test_config_applications_dir_dev_default_backcompat(monkeypatch):
    # With the repo-default data_dir, applications stays at the historical
    # REPO_ROOT/applications so the author's existing local drafts aren't orphaned.
    for var in ("JOB_APPLIER_DATA_DIR", "JOB_APPLIER_APPLICATIONS_DIR"):
        monkeypatch.delenv(var, raising=False)
    s = _clean_settings()
    assert s.applications_dir == REPO_ROOT / "applications"
    # But the same repo-default derivation for a relocated dir does NOT special-case.
    s2 = _clean_settings(data_dir=REPO_ROOT / "data" / "copy")
    assert s2.applications_dir == REPO_ROOT / "data" / "copy" / "applications"


def test_config_defaults_unchanged(monkeypatch):
    # Live `main`-style use: no JOB_APPLIER_* env -> paths under REPO_ROOT/data.
    for var in ("JOB_APPLIER_DATA_DIR", "JOB_APPLIER_DB_PATH", "JOB_APPLIER_RESUMES_DIR"):
        monkeypatch.delenv(var, raising=False)
    s = _clean_settings()
    assert s.db_path == REPO_ROOT / "data" / "jobs.db"
    assert s.resumes_dir == REPO_ROOT / "data" / "resumes"
    assert s.applications_dir == REPO_ROOT / "applications"


def test_serve_prod_disables_reload(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: captured.update(app=a, **k))

    cli.serve(host=None, port=None, prod=True)
    assert captured["reload"] is False

    captured.clear()
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: captured.update(app=a, **k))
    cli.serve(host=None, port=None, prod=False)
    assert captured["reload"] is True


def test_serve_uses_explicit_port(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: captured.update(k))
    cli.serve(host=None, port=54321, prod=True)
    assert captured["port"] == 54321


def test_cors_allows_loopback_ports():
    client = TestClient(app)
    origin = "http://127.0.0.1:53112"
    resp = client.get("/api/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_foreign_origin():
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 200
    # Non-loopback, non-configured origin gets no allow-origin echo.
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"


def test_free_port_is_usable():
    import socket

    port = cli._free_port()
    assert 1024 < port < 65536
    # Nothing else grabbed it; we can bind it right after.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))
