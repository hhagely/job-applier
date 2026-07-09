"""Phase 7 / Workstreams B + E: version source of truth and the update check."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from job_applier import __version__, updates
from job_applier.api.app import app

_REPO_ROOT = Path(__file__).resolve().parents[1]

client = TestClient(app)


# --- Workstream B: single version source of truth --------------------------


def test_version_endpoint_returns_canonical():
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": __version__}


def test_health_includes_version():
    resp = client.get("/api/health")
    assert resp.json()["version"] == __version__


def test_pyproject_version_agrees():
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "no version in pyproject.toml"
    assert m.group(1) == __version__


def test_stamp_makes_package_json_agree():
    """The stamp step is what keeps desktop/package.json in lockstep with
    __version__; after running it, package.json must match the canonical version."""
    spec = importlib.util.spec_from_file_location(
        "stamp_version", _REPO_ROOT / "desktop" / "scripts" / "stamp_version.py"
    )
    stamp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp_mod)
    assert stamp_mod.read_version() == __version__

    stamp_mod.stamp()  # idempotent; leaves package.json stamped to __version__
    pkg = json.loads((_REPO_ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == __version__


# --- Workstream E: update check --------------------------------------------


@pytest.fixture(autouse=True)
def _clear_update_cache():
    updates._reset_cache_for_tests()
    yield
    updates._reset_cache_for_tests()


def _mock_latest(monkeypatch, tag):
    monkeypatch.setattr(updates, "_fetch_latest_tag", lambda: tag)


def test_update_available_when_newer(monkeypatch):
    _mock_latest(monkeypatch, "v999.0.0")
    resp = client.get("/api/update")
    body = resp.json()
    assert body["update_available"] is True
    assert body["latest"] == "v999.0.0"
    assert body["current"] == __version__
    assert "releases" in body["url"]


def test_no_update_when_same(monkeypatch):
    _mock_latest(monkeypatch, f"v{__version__}")
    assert client.get("/api/update").json()["update_available"] is False


def test_no_update_when_older(monkeypatch):
    _mock_latest(monkeypatch, "v0.0.1")
    assert client.get("/api/update").json()["update_available"] is False


def test_soft_fail_on_network_error(monkeypatch):
    def boom():
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(updates, "_fetch_latest_tag", boom)
    body = client.get("/api/update").json()
    assert body["update_available"] is False
    assert body["current"] == __version__


def test_result_is_cached(monkeypatch):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return "v999.0.0"

    monkeypatch.setattr(updates, "_fetch_latest_tag", counting)
    updates.check_for_update()
    updates.check_for_update()
    assert calls["n"] == 1  # second read served from cache


@pytest.mark.parametrize(
    "current,latest,expected",
    [
        ("0.1.0", "v0.2.0", True),
        ("0.1.0", "v0.1.0", False),
        ("0.2.0", "v0.1.9", False),
        ("1.0.0", "v1.0.0-rc1", False),  # pre-release suffix stripped -> equal
        ("0.9.0", "v0.10.0", True),  # numeric, not lexical
        ("0.1.0", "garbage", False),  # unparseable -> (0,) -> not newer
    ],
)
def test_version_compare(current, latest, expected):
    assert (updates._Version.parse(current) < updates._Version.parse(latest)) is expected
