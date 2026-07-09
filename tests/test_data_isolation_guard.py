"""Phase 7 / Workstream A2: the build-time data-isolation guard.

The guard (`desktop/scripts/check_no_personal_data.py`) is what stops a build run
on the author's machine — where real `data/` and `applications/` exist at the repo
root — from ever shipping personal data in an installer. These tests pin its two
directions: it flags DB / resumes / draft payloads, and it stays quiet on a clean
packaged tree (shell code + frozen backend + web build).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_GUARD = Path(__file__).resolve().parents[1] / "desktop" / "scripts" / "check_no_personal_data.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_no_personal_data", _GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


def test_flags_sqlite_db(tmp_path):
    (tmp_path / "resources" / "backend").mkdir(parents=True)
    (tmp_path / "resources" / "backend" / "jobs.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "resources" / "backend" / "jobs.db-journal").write_bytes(b"x")
    (tmp_path / "resources" / "backend" / "jobs.db.bak").write_bytes(b"x")
    findings = guard.scan(tmp_path)
    assert len(findings) == 3
    assert all("SQLite database" in f for f in findings)


def test_flags_resumes_payload(tmp_path):
    resumes = tmp_path / "resources" / "resumes"
    resumes.mkdir(parents=True)
    (resumes / "abc123.pdf").write_bytes(b"%PDF")
    findings = guard.scan(tmp_path)
    assert any("resumes/ payload" in f for f in findings)


def test_flags_applications_drafts(tmp_path):
    drafts = tmp_path / "resources" / "applications" / "2971"
    drafts.mkdir(parents=True)
    (drafts / "resume.md").write_text("# resume")
    findings = guard.scan(tmp_path)
    assert any("applications/<id>/ drafts" in f for f in findings)


def test_clean_packaged_tree_passes(tmp_path):
    # Mirror the real electron-builder output: shell code + frozen backend + web build.
    (tmp_path / "resources" / "backend").mkdir(parents=True)
    (tmp_path / "resources" / "backend" / "job-applier-backend").write_bytes(b"\x7fELF")
    web = tmp_path / "resources" / "web"
    web.mkdir(parents=True)
    (web / "handler.js").write_text("export const handler = () => {};")
    (tmp_path / "main.js").write_text("// electron main")
    assert guard.scan(tmp_path) == []


def test_empty_resumes_dir_is_not_a_payload(tmp_path):
    # A bare `resumes/` with no files (e.g. a lazily-created runtime dir) is not
    # personal data; only a dir holding files counts.
    (tmp_path / "resumes").mkdir()
    assert guard.scan(tmp_path) == []


def test_missing_root_is_clean(tmp_path):
    assert guard.scan(tmp_path / "does-not-exist") == []


def test_main_exit_codes(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    assert guard.main(["prog", str(clean)]) == 0

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "jobs.db").write_bytes(b"x")
    assert guard.main(["prog", str(dirty)]) == 1
