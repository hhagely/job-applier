"""Legacy-DB migration coverage for the hand-rolled ``_ensure_*`` helpers.

The project deliberately uses no alembic: schema changes are idempotent
``ALTER TABLE ... ADD COLUMN`` helpers in ``models/db.py`` run on every startup
from ``create_db_and_tables()``. These tests build a pre-migration DB (tables
missing the newer columns), run the startup path, and assert the columns +
indexes get added — the exact regression the strategy is most exposed to
("added a field, forgot the helper").

``_LEGACY_SCHEMA`` below must name **every** table the models declare, because
the generic parity guard can only protect a table that exists in the "before"
state — ``create_all`` silently builds any table that's missing, which is
exactly what hides the bug on a fresh install. A table that's brand new today
is a legacy table the moment it ships, so it belongs here from the start.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

# Every table as it existed BEFORE the current crop of migrations:
# jobposting predates cross_source_hash + jd_fingerprint/duplicate_of;
# matchscore predates resume_id; both score tables predate score_kind
# (matchscorehistory shipped with resume_id); searchprofile predates
# home_state; sourceslug predates added_by_user/label; application predates
# the followup and unemployment columns. company / resume / appsetting /
# blacklistedcompany have never needed a helper and so appear at their
# shipped shape — they're here so that the first column added to any of them
# without a helper trips the parity guard below.
#
# create_all() is a no-op for tables that already exist, so this whole script
# stands in for a real user's upgraded-in-place database.
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
CREATE TABLE searchprofile (
    id INTEGER PRIMARY KEY,
    role_titles JSON,
    seniority_terms JSON,
    required_tech JSON,
    excluded_tech JSON,
    extracted_skills JSON,
    recommendations_draft JSON,
    updated_at DATETIME
);
CREATE TABLE sourceslug (
    id INTEGER PRIMARY KEY,
    source VARCHAR NOT NULL,
    slug VARCHAR NOT NULL,
    enabled BOOLEAN,
    last_fetched_at DATETIME,
    last_job_count INTEGER,
    last_error VARCHAR,
    added_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE application (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    notes VARCHAR,
    applied_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE company (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    domain VARCHAR,
    is_blocked BOOLEAN,
    notes VARCHAR
);
CREATE TABLE resume (
    id INTEGER PRIMARY KEY,
    original_filename VARCHAR NOT NULL,
    pdf_path VARCHAR NOT NULL,
    extracted_text VARCHAR NOT NULL,
    page_count INTEGER,
    is_active BOOLEAN,
    uploaded_at DATETIME
);
CREATE TABLE appsetting (
    key VARCHAR NOT NULL PRIMARY KEY,
    value VARCHAR NOT NULL
);
CREATE TABLE blacklistedcompany (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    normalized_name VARCHAR NOT NULL,
    reason VARCHAR,
    created_at DATETIME
);
"""

_LEGACY_TABLES = frozenset(re.findall(r"CREATE TABLE (\w+)", _LEGACY_SCHEMA))


def _guarded_tables() -> list[str]:
    """Table names the parity guard runs against: derived from the model metadata
    (never hand-listed, so a new table can't quietly opt out) and intersected with
    the legacy DB, since a table absent from the "before" state proves nothing."""
    from sqlmodel import SQLModel

    from job_applier.models import db  # noqa: F401 — registers every table

    return sorted(t.name for t in SQLModel.metadata.sorted_tables if t.name in _LEGACY_TABLES)


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


def test_migration_adds_searchprofile_home_state(tmp_path, monkeypatch):
    # A legacy searchprofile row (pre-home_state) gets the nullable column added,
    # so an upgraded DB can store a home state instead of silently lacking it.
    db_path, _ = _run_startup(tmp_path, monkeypatch)
    assert "home_state" in _cols(db_path, "searchprofile")


def test_migration_adds_sourceslug_whitelist_columns(tmp_path, monkeypatch):
    # Every pre-migration row got into the table via the seed or feed discovery,
    # so the backfill must read as "not added by hand" — otherwise the /search
    # whitelist would list back the user's whole thousand-slug discovered set.
    db_path, _ = _run_startup(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO sourceslug (source, slug, enabled) VALUES ('gh','a',1)")
        conn.commit()
        assert conn.execute("SELECT added_by_user, label FROM sourceslug").fetchone() == (
            0,
            None,
        )
    finally:
        conn.close()
    assert {"added_by_user", "label"}.issubset(_cols(db_path, "sourceslug"))
    assert "ix_sourceslug_added_by_user" in _indexes(db_path, "sourceslug")


def _schema_snapshot(path) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    return {
        table: (frozenset(_cols(path, table)), frozenset(_indexes(path, table)))
        for table in sorted(_LEGACY_TABLES)
    }


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """A second startup on the already-migrated DB is a clean no-op (every helper
    guards its ALTER behind a PRAGMA membership check).

    Indexes are snapshotted alongside columns: a helper whose ALTER is guarded but
    whose CREATE INDEX is not would leave the column set identical, so a
    columns-only comparison would wave it through.
    """
    db_path, models = _run_startup(tmp_path, monkeypatch)
    before = _schema_snapshot(db_path)

    models.db.create_db_and_tables()  # run again — must not raise

    assert _schema_snapshot(db_path) == before


def test_legacy_schema_covers_every_model_table():
    """The parity guard below is only as wide as ``_LEGACY_SCHEMA``: a table that
    isn't in the legacy DB gets built fresh by ``create_all``, which is precisely
    the state that hides a missing migration. So every model table must be
    declared above — a brand-new table needs no ALTER today, but the *next*
    column added to it does, and by then a real user's DB will have it.
    """
    from sqlmodel import SQLModel

    from job_applier.models import db  # noqa: F401 — registers every table

    model_tables = {t.name for t in SQLModel.metadata.sorted_tables}
    assert not model_tables - _LEGACY_TABLES, (
        f"model tables missing from _LEGACY_SCHEMA: {sorted(model_tables - _LEGACY_TABLES)} "
        f"— add each one at its currently-shipped shape so the parity guard covers it."
    )
    assert not _LEGACY_TABLES - model_tables, (
        f"_LEGACY_SCHEMA declares tables the models no longer have: "
        f"{sorted(_LEGACY_TABLES - model_tables)}"
    )


@pytest.mark.parametrize("table", _guarded_tables())
def test_migrated_legacy_tables_have_every_model_column(tmp_path, monkeypatch, table):
    """Generic parity guard against "added a model column, forgot the _ensure_*
    helper". A fresh install hides that bug (create_all builds the current models),
    but a user's upgraded-in-place DB is left missing the column. So we migrate a
    legacy DB and assert every column the model declares is now present. Add a
    column without a helper and this fails, where the per-helper tests above
    (which check specific columns) would not notice the new one.

    The table list is derived from the model metadata rather than hand-listed —
    the hand-listed version covered 5 of 10 tables and left ``application``
    (already on two helpers), ``company``, and ``resume`` unprotected.
    """
    from sqlmodel import SQLModel

    db_path, _ = _run_startup(tmp_path, monkeypatch)

    model_cols = {c.name for c in SQLModel.metadata.tables[table].columns}
    missing = model_cols - _cols(db_path, table)
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
