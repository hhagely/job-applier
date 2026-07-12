"""SQLite engine hardening: WAL journal mode + a busy timeout on every connection.

Regression guard for the "database is locked" 500s. SQLite defaults to
busy_timeout=0 and a rollback journal, so any write-lock contention (the
background scorer/ingest writing while a request commits, or the synchronous
suggest-roles endpoint holding its transaction open across the ~45s LLM call)
raised an uncaught OperationalError that surfaced as an opaque HTTP 500. The
engine now sets WAL + a 30s busy timeout via a connect-time PRAGMA hook.
"""

from __future__ import annotations


def _fresh_engine(tmp_path, monkeypatch):
    from job_applier import models
    from job_applier.config import settings

    monkeypatch.setattr(settings, "db_path", tmp_path / "engine.db")
    monkeypatch.setattr(models.db, "_engine", None)
    return models.db.engine()


def test_engine_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    eng = _fresh_engine(tmp_path, monkeypatch)
    with eng.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30000


def test_busy_timeout_applies_to_a_second_connection(tmp_path, monkeypatch):
    """The PRAGMA runs on every new pooled connection, not just the first — so a
    contending writer waits for the lock instead of erroring immediately."""
    eng = _fresh_engine(tmp_path, monkeypatch)
    with eng.connect() as first, eng.connect() as second:
        assert first.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30000
        assert second.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30000
