from __future__ import annotations

import atexit
import signal
import subprocess
import time
import webbrowser

import pytest
import typer
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


# ---- app-dev launcher supervision loop -------------------------------------


class _FakeProc:
    """Stand-in for a spawned child: alive until `exits_after` poll() calls."""

    def __init__(self, exits_after: int | None = None) -> None:
        self.exits_after = exits_after
        self.polls = 0
        self.terminated = False

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            return 0
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:  # pragma: no cover - only on a wait() timeout
        self.terminated = True


def _stub_app_dev(monkeypatch, tmp_path, *, procs, sleep) -> list:
    """Run `app-dev` against fake children, with no `signal.pause` available.

    Deleting `signal.pause` reproduces Windows, where the attribute genuinely does
    not exist — so the supervision loop has to idle some other way on every
    platform this suite runs on. Returns the list of atexit hooks the launcher
    registered (captured instead of really registered, so pytest's own exit
    doesn't run them).
    """
    monkeypatch.delattr(signal, "pause", raising=False)
    (tmp_path / "web" / "build").mkdir(parents=True)
    (tmp_path / "web" / "build" / "index.js").write_text("// built web server")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)

    spawned = iter(procs)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: next(spawned))
    monkeypatch.setattr(cli, "_wait_for_health", lambda *a, **k: True)
    monkeypatch.setattr(webbrowser, "open", lambda url: True)
    # Don't leave the launcher's SIGINT handler / atexit hook installed in the
    # pytest process.
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
    hooks: list = []
    monkeypatch.setattr(atexit, "register", lambda fn: hooks.append(fn) or fn)
    monkeypatch.setattr(time, "sleep", sleep)
    return hooks


def test_app_dev_supervises_children_without_signal_pause(monkeypatch, tmp_path):
    """The loop must idle with a sleep, not `signal.pause()` (Unix-only): on
    Windows the first pass raised AttributeError, which escaped the
    `except KeyboardInterrupt` and tore down the servers the launcher had just
    opened a browser tab against."""
    naps: list[float] = []
    api = _FakeProc()
    web = _FakeProc(exits_after=2)
    hooks = _stub_app_dev(monkeypatch, tmp_path, procs=[api, web], sleep=naps.append)

    with pytest.raises(typer.Exit) as exc:
        cli.app_dev()

    # Survived several passes, then noticed the dead child and shut down.
    assert exc.value.exit_code == 1
    assert naps and all(0 < n <= 2 for n in naps)
    # Teardown at process exit still reaps the child that is still running.
    for hook in hooks:
        hook()
    assert api.terminated


def test_app_dev_exits_cleanly_on_ctrl_c(monkeypatch, tmp_path):
    def _interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    api = _FakeProc()
    web = _FakeProc()
    _stub_app_dev(monkeypatch, tmp_path, procs=[api, web], sleep=_interrupt)

    cli.app_dev()  # Ctrl-C during the nap is a normal quit, not a traceback.
