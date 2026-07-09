"""Legacy-DB migration coverage for the hand-rolled ``_ensure_*`` helpers.

The project deliberately uses no alembic: schema changes are idempotent
``ALTER TABLE ... ADD COLUMN`` helpers in ``models/db.py`` run on every startup
from ``create_db_and_tables()``. These tests build a pre-migration DB (tables
missing the newer columns), run the startup path, and assert the columns +
indexes get added — the exact regression the strategy is most exposed to
("added a field, forgot the helper"). The ``application`` column migrations are
already covered by test_followups / test_unemployment; this file covers the
other four helpers.
"""

from __future__ import annotations

import sqlite3

# jobposting / matchscore / matchscorehistory as they existed BEFORE the
# cross_source_hash, jd_fingerprint/duplicate_of, resume_id, and score_kind
# columns were added. matchscorehistory shipped with resume_id but predates
# score_kind. create_all() is a no-op for tables that already exist, so these
# stand in for a real user's upgraded-in-place database.
_LEGACY_SCHEMA = """
CREATE TABLE jobposting (
    id INTEGER PRIMARY KEY,
    source VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    description VARCHAR,
    location VARCHAR,
    remote BOOLEAN,
    employment_type VARCHAR,
    posted_at DATETIME,
    ingested_at DATETIME,
    dedupe_hash VARCHAR,
    raw JSON,
    filter_status VARCHAR,
    filter_reason VARCHAR,
    company_id INTEGER
);
CREATE TABLE matchscore (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    rubric JSON,
    reasoning VARCHAR,
    scored_by VARCHAR,
    scored_at DATETIME
);
CREATE TABLE matchscorehistory (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    rubric JSON,
    reasoning VARCHAR,
    scored_by VARCHAR,
    scored_at DATETIME,
    resume_id INTEGER
);
"""


def _make_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.commit()
    conn.close()


def _cols(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _indexes(path, table):
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            )
        }
    finally:
        conn.close()


def _run_startup(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    from job_applier import models
    from job_applier.config import settings

    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(models.db, "_engine", None)
    models.db.create_db_and_tables()
    return db_path, models


def test_migration_adds_jobposting_dedupe_columns(tmp_path, monkeypatch):
    db_path, _ = _run_startup(tmp_path, monkeypatch)

    cols = _cols(db_path, "jobposting")
    assert {"cross_source_hash", "jd_fingerprint", "duplicate_of"}.issubset(cols)

    idx = _indexes(db_path, "jobposting")
    assert "ix_jobposting_cross_source_hash" in idx
    assert "ix_jobposting_jd_fingerprint" in idx
    assert "ix_jobposting_duplicate_of" in idx


def test_migration_adds_matchscore_resume_id(tmp_path, monkeypatch):
    db_path, _ = _run_startup(tmp_path, monkeypatch)
    assert "resume_id" in _cols(db_path, "matchscore")


def test_migration_adds_score_kind_to_both_score_tables(tmp_path, monkeypatch):
    db_path, _ = _run_startup(tmp_path, monkeypatch)
    assert "score_kind" in _cols(db_path, "matchscore")
    assert "score_kind" in _cols(db_path, "matchscorehistory")
    assert "ix_matchscore_score_kind" in _indexes(db_path, "matchscore")
    assert "ix_matchscorehistory_score_kind" in _indexes(db_path, "matchscorehistory")


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """A second startup on the already-migrated DB is a clean no-op (every helper
    guards its ALTER behind a PRAGMA membership check)."""
    db_path, models = _run_startup(tmp_path, monkeypatch)
    before = _cols(db_path, "jobposting") | _cols(db_path, "matchscore")

    models.db.create_db_and_tables()  # run again — must not raise

    after = _cols(db_path, "jobposting") | _cols(db_path, "matchscore")
    assert before == after


def _model_columns(model) -> set[str]:
    return {c.name for c in model.__table__.columns}


def test_migrated_legacy_tables_have_every_model_column(tmp_path, monkeypatch):
    """Generic parity guard against "added a model column, forgot the _ensure_*
    helper". A fresh install hides that bug (create_all builds the current models),
    but a user's upgraded-in-place DB is left missing the column. So we migrate a
    legacy DB and assert every column the model declares is now present on the
    tables that receive migrations. Add a column to one of these without a helper
    and this fails, where the per-helper tests above (which check specific columns)
    would not notice the new one.
    """
    db_path, _ = _run_startup(tmp_path, monkeypatch)
    from job_applier.models.db import JobPosting, MatchScore, MatchScoreHistory

    for model, table in (
        (JobPosting, "jobposting"),
        (MatchScore, "matchscore"),
        (MatchScoreHistory, "matchscorehistory"),
    ):
        missing = _model_columns(model) - _cols(db_path, table)
        assert not missing, (
            f"{table} is missing {sorted(missing)} after migration — add an "
            f"_ensure_* helper for it in models/db.py (a fresh install would hide "
            f"this, an upgraded DB would not)."
        )


def test_no_ensure_helper_is_orphaned():
    """Every ``_ensure_*`` migration helper defined in models/db.py must be called
    from ``create_db_and_tables``; an orphaned helper silently skips its migration
    on every existing DB."""
    import inspect

    from job_applier.models import db

    helpers = [
        name
        for name in dir(db)
        if name.startswith("_ensure_") and callable(getattr(db, name))
    ]
    assert helpers, "expected _ensure_* migration helpers in models/db.py"
    startup_src = inspect.getsource(db.create_db_and_tables)
    orphaned = [h for h in helpers if h not in startup_src]
    assert not orphaned, f"migration helpers never called from startup: {orphaned}"
