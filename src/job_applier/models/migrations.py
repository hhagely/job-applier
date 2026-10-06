"""Startup migrations: idempotent ``_ensure_*`` helpers, run by ``run()`` from
``create_db_and_tables`` on every start (no alembic).

Each helper checks ``PRAGMA table_info`` and only alters what's missing. A
one-time backfill (one gated on a column being absent) opens an explicit
transaction with ``_begin`` first: pysqlite autocommits DDL otherwise, and a
crash between the ALTER and the backfill would skip the backfill forever.
"""

from enum import Enum
from typing import Optional

from sqlalchemy.engine import Connection
from sqlmodel import SQLModel

from job_applier.models.db import engine
from job_applier.models.scoping import _active_profile_id_from, _insert_default_profile


def run() -> None:
    """Bring an existing DB up to the models' shape (after ``create_all``)."""
    _ensure_cross_source_hash_column()
    _ensure_matchscore_resume_id_column()
    _ensure_score_kind_columns()
    _ensure_application_followup_columns()
    _ensure_application_unemployment_columns()
    _ensure_jd_dedupe_columns()
    _ensure_searchprofile_columns()
    # After create_all (it backfills into the new jobprofilelink table).
    _ensure_multi_profile_columns()
    _ensure_sourceslug_columns()
    # Last: rebuilds tables, so every column helper above must have run first.
    _ensure_per_profile_state()
    _ensure_match_columns()
    _ensure_resume_owner()


def _table_cols(conn: Connection, table: str) -> set[str]:
    return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}

def _reset_posting_verdicts(conn: Connection) -> None:
    """After legacy verdicts are copied onto the links, leave the posting columns
    meaning only the shared verdict (every stored posting passed it)."""
    conn.exec_driver_sql(
        "UPDATE jobposting SET filter_status = 'passed', filter_reason = NULL "
        "WHERE filter_status IS NOT 'passed' OR filter_reason IS NOT NULL"
    )

def _begin(conn: Connection) -> None:
    """Open a write transaction explicitly so DDL joins it (see the callers)."""
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def _ensure_cross_source_hash_column() -> None:
    """Add JobPosting.cross_source_hash on existing DBs that pre-date the column.

    SQLModel.metadata.create_all is a no-op for tables that already exist, so
    ALTER TABLE here covers the migration path. Cheap to call every startup.
    """
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobposting)")}
        if "cross_source_hash" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE jobposting ADD COLUMN cross_source_hash VARCHAR"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobposting_cross_source_hash "
                "ON jobposting (cross_source_hash)"
            )
            conn.commit()


def _ensure_matchscore_resume_id_column() -> None:
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(matchscore)")}
        if "resume_id" not in cols:
            # Carry the FK so migrated DBs match the fresh-install shape
            # (model declares foreign_key="resume.id") and duplicate_of's pattern.
            conn.exec_driver_sql(
                "ALTER TABLE matchscore ADD COLUMN resume_id INTEGER REFERENCES resume(id)"
            )
            conn.commit()


def _ensure_jd_dedupe_columns() -> None:
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobposting)")}
        if "jd_fingerprint" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE jobposting ADD COLUMN jd_fingerprint VARCHAR"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobposting_jd_fingerprint "
                "ON jobposting (jd_fingerprint)"
            )
        if "duplicate_of" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE jobposting ADD COLUMN duplicate_of INTEGER "
                "REFERENCES jobposting(id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobposting_duplicate_of "
                "ON jobposting (duplicate_of)"
            )
        conn.commit()


def _ensure_searchprofile_columns() -> None:
    """Add SearchProfile.home_state / title_terms on existing DBs that pre-date them.

    home_state is nullable with no default: existing profiles migrate to "no home
    state set", which skips the state-allow-list rule until the user picks a state
    at /search. title_terms defaults to an empty list, which skips the title gate,
    so existing profiles match exactly as before until the user sets keywords.
    """
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(searchprofile)")}
        if "home_state" not in cols:
            conn.exec_driver_sql("ALTER TABLE searchprofile ADD COLUMN home_state VARCHAR")
        if "title_terms" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE searchprofile ADD COLUMN title_terms JSON NOT NULL DEFAULT '[]'"
            )
        conn.commit()


def _ensure_multi_profile_columns() -> None:
    """Migrate a single-profile DB to named, switchable profiles.

    Runs its one-time backfill only on the startup that adds ``is_active``: the
    existing row becomes the active "Default" profile, pointed at the active
    resume, and every existing posting is linked to it (the pre-migration queue
    *was* that profile's queue). A DB with postings but no profile gets a Default
    row first so those postings aren't orphaned. Gating on the column add keeps
    this from re-homing postings later — a deleted profile's postings stay
    unlinked rather than being swept into whichever profile is active.
    """
    with engine().connect() as conn:
        cols = _table_cols(conn, "searchprofile")
        if {"name", "resume_id", "is_active"} <= cols:
            return
        # One transaction for the ALTERs and the backfill: the backfill is gated
        # on ``is_active`` being absent, so a crash between them must not leave
        # the column behind with nothing linked.
        _begin(conn)
        if "name" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE searchprofile ADD COLUMN name VARCHAR NOT NULL DEFAULT 'Default'"
            )
        if "resume_id" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE searchprofile ADD COLUMN resume_id INTEGER REFERENCES resume(id)"
            )
        if "is_active" in cols:
            conn.commit()
            return
        conn.exec_driver_sql(
            "ALTER TABLE searchprofile ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 0"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_searchprofile_is_active "
            "ON searchprofile (is_active)"
        )
        has_jobs = conn.exec_driver_sql("SELECT 1 FROM jobposting LIMIT 1").first()
        has_profile = conn.exec_driver_sql("SELECT 1 FROM searchprofile LIMIT 1").first()
        if has_jobs and not has_profile:
            _insert_default_profile(conn)
        first = conn.exec_driver_sql("SELECT MIN(id) FROM searchprofile").scalar()
        if first is not None:
            conn.exec_driver_sql(
                "UPDATE searchprofile SET is_active = 1, resume_id = "
                "(SELECT id FROM resume WHERE is_active = 1 LIMIT 1) WHERE id = ?",
                (first,),
            )
            # A DB coming straight from a pre-profile release gets jobprofilelink
            # from create_all at today's shape (profile_id + a NOT NULL verdict);
            # one migrated by the first multi-profile build still has the old
            # column, which _ensure_match_columns renames afterwards.
            if "profile_id" in _table_cols(conn, "jobprofilelink"):
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO jobprofilelink "
                    "(job_id, profile_id, filter_status, filter_reason, linked_at) "
                    "SELECT id, ?, COALESCE(filter_status, 'passed'), filter_reason, CURRENT_TIMESTAMP "
                    "FROM jobposting",
                    (first,),
                )
                _reset_posting_verdicts(conn)
            else:
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO jobprofilelink (job_id, search_profile_id, linked_at) "
                    "SELECT id, ?, CURRENT_TIMESTAMP FROM jobposting",
                    (first,),
                )
        conn.commit()


def _ensure_sourceslug_columns() -> None:
    """Add SourceSlug.added_by_user / .label on existing DBs that pre-date them.

    Everything already in the table got there via the seed or feed discovery, so
    the DEFAULT 0 backfill is the truthful value: no existing row was added by
    hand. ``label`` stays nullable — the UI falls back to the slug.
    """
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sourceslug)")}
        added = False
        if "added_by_user" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE sourceslug ADD COLUMN added_by_user "
                "BOOLEAN NOT NULL DEFAULT 0"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_sourceslug_added_by_user "
                "ON sourceslug (added_by_user)"
            )
            added = True
        if "label" not in cols:
            conn.exec_driver_sql("ALTER TABLE sourceslug ADD COLUMN label VARCHAR")
            added = True
        if added:
            conn.commit()


def _ensure_score_kind_columns() -> None:
    with engine().connect() as conn:
        for table in ("matchscore", "matchscorehistory"):
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if "score_kind" not in cols:
                # NOT NULL to match the model's non-Optional `score_kind: str`
                # (fresh installs build it NOT NULL); the DEFAULT backfills the
                # existing rows so the NOT NULL is satisfied on migrated DBs.
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN score_kind VARCHAR "
                    "NOT NULL DEFAULT 'baseline'"
                )
                conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_score_kind "
                    f"ON {table} (score_kind)"
                )
        conn.commit()


def _ensure_application_followup_columns() -> None:
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(application)")}
        added = False
        if "next_followup_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE application ADD COLUMN next_followup_at DATETIME")
            added = True
        if "last_contact_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE application ADD COLUMN last_contact_at DATETIME")
            added = True
        if "outcome" not in cols:
            conn.exec_driver_sql("ALTER TABLE application ADD COLUMN outcome VARCHAR")
            added = True
        if added:
            conn.commit()


def _ensure_application_unemployment_columns() -> None:
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(application)")}
        added = False
        if "used_for_unemployment" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE application ADD COLUMN used_for_unemployment "
                "BOOLEAN NOT NULL DEFAULT 0"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_application_used_for_unemployment "
                "ON application (used_for_unemployment)"
            )
            added = True
        if "used_for_unemployment_at" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE application ADD COLUMN used_for_unemployment_at DATETIME"
            )
            added = True
        if added:
            conn.commit()


def _ensure_per_profile_state() -> None:
    """Give applications, scores, score history and the blacklist an owner profile.

    One-time, gated on ``profile_id`` being absent. Every existing row goes to the
    active profile (the Default that ``_ensure_multi_profile_columns`` made), which
    is created here if per-profile rows exist with no profile yet.

    ``application`` and ``matchscore`` shipped with an inline ``UNIQUE (job_id)``
    that must become ``UNIQUE (job_id, profile_id)``; SQLite can't drop a table
    constraint with ALTER, so those two are rebuilt (see ``_rebuild_with_profile``).
    The other two only need the column, plus the blacklist's unique index moving
    from ``normalized_name`` to ``(profile_id, normalized_name)``.
    """
    with engine().connect() as conn:

        todo = [
            t
            for t in ("application", "matchscore", "matchscorehistory", "blacklistedcompany")
            if "profile_id" not in _table_cols(conn, t)
        ]
        if not todo:
            return
        # SQLite DDL is transactional, but pysqlite only opens a transaction
        # before DML, so without this the rename/create in the rebuild would
        # autocommit and a failed copy would strand rows in ``*__pre_profile``.
        _begin(conn)
        pid = _active_profile_id_from(conn)
        if pid is None and any(
            conn.exec_driver_sql(f"SELECT 1 FROM {t} LIMIT 1").first() for t in todo
        ):
            pid = _insert_default_profile(conn)

        for table in ("application", "matchscore"):
            if table in todo:
                _rebuild_with_profile(conn, table, pid, _table_cols(conn, table))
        for table in ("matchscorehistory", "blacklistedcompany"):
            if table in todo:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN profile_id INTEGER "
                    "REFERENCES searchprofile(id)"
                )
                conn.exec_driver_sql(f"UPDATE {table} SET profile_id = ?", (pid,))
                conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_profile_id ON {table} (profile_id)"
                )
        if "blacklistedcompany" in todo:
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_blacklistedcompany_normalized_name")
            conn.exec_driver_sql(
                "CREATE INDEX ix_blacklistedcompany_normalized_name "
                "ON blacklistedcompany (normalized_name)"
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_blacklistedcompany_profile_name "
                "ON blacklistedcompany (profile_id, normalized_name)"
            )
        conn.commit()


def _rebuild_with_profile(
    conn: Connection, table: str, pid: Optional[int], old_cols: set[str]
) -> None:
    """Recreate ``table`` at the model's current shape, copying rows across with
    ``profile_id = pid``. SQLite's documented rebuild: rename the old table aside,
    create the new one from the SQLModel metadata (so a migrated DB can't drift
    from a fresh install), copy, drop the old. Runs inside the caller's
    explicit transaction, so a failure part-way leaves the original table in place.

    Foreign-key enforcement is never switched on for this engine (no
    ``PRAGMA foreign_keys``), so the rename/drop can't trip a constraint, and no
    other table references these two.
    """
    old = f"{table}__pre_profile"
    conn.exec_driver_sql(f"ALTER TABLE {table} RENAME TO {old}")
    # The old indexes follow the rename but keep their names, which the new
    # table's indexes reuse. Auto-indexes (sql IS NULL) go with the table.
    for (name,) in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ? "
        "AND sql IS NOT NULL",
        (old,),
    ).all():
        conn.exec_driver_sql(f"DROP INDEX {name}")
    SQLModel.metadata.tables[table].create(conn)
    copied = [c for c in SQLModel.metadata.tables[table].columns if c.name in old_cols]
    # Legacy columns that were nullable are NOT NULL now, so fill their NULLs with
    # the model's default rather than failing the copy.
    select_list, params = [], []
    for c in copied:
        default = c.default
        if c.nullable or default is None:
            select_list.append(c.name)
        elif default.is_callable:
            select_list.append(f"COALESCE({c.name}, CURRENT_TIMESTAMP)")
        else:
            value = default.arg
            select_list.append(f"COALESCE({c.name}, ?)")
            params.append(value.name if isinstance(value, Enum) else value)
    conn.exec_driver_sql(
        f"INSERT INTO {table} ({', '.join(c.name for c in copied)}, profile_id) "
        f"SELECT {', '.join(select_list)}, ? FROM {old}",
        (*params, pid),
    )
    conn.exec_driver_sql(f"DROP TABLE {old}")


def _ensure_match_columns() -> None:
    """Scrape-once / match-per-profile columns (PR 2 of multi-profile).

    - ``jobprofilelink.search_profile_id`` is renamed ``profile_id`` so the link
      table is scoped like every other per-profile table.
    - ``jobprofilelink.filter_status`` / ``filter_reason`` hold each profile's own
      verdict. Existing links were all written by an ingest that *was* that
      profile's filter, so they backfill from the posting's columns.
    - ``jobposting.tags`` keeps the source tags so later matching sees what
      ingest saw. Existing postings stay null (treated as no tags).

    The verdict backfill is one-time (gated on the column), so everything runs in
    one explicit transaction: a crash part-way can't leave the column added with
    the backfill lost.
    """
    with engine().connect() as conn:
        link_cols = _table_cols(conn, "jobprofilelink")
        has_tags = "tags" in _table_cols(conn, "jobposting")
        if "filter_status" in link_cols and "profile_id" in link_cols and has_tags:
            return
        _begin(conn)
        if "search_profile_id" in link_cols and "profile_id" not in link_cols:
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_jobprofilelink_search_profile_id")
            conn.exec_driver_sql(
                "ALTER TABLE jobprofilelink RENAME COLUMN search_profile_id TO profile_id"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobprofilelink_profile_id "
                "ON jobprofilelink (profile_id)"
            )
        if "filter_status" not in link_cols:
            conn.exec_driver_sql(
                "ALTER TABLE jobprofilelink ADD COLUMN filter_status VARCHAR(7) "
                "NOT NULL DEFAULT 'passed'"
            )
            conn.exec_driver_sql("ALTER TABLE jobprofilelink ADD COLUMN filter_reason VARCHAR")
            conn.exec_driver_sql(
                "UPDATE jobprofilelink SET "
                "filter_status = COALESCE((SELECT filter_status FROM jobposting WHERE jobposting.id = job_id), 'passed'), "
                "filter_reason = (SELECT filter_reason FROM jobposting WHERE jobposting.id = job_id)"
            )
            _reset_posting_verdicts(conn)
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobprofilelink_profile_status "
                "ON jobprofilelink (profile_id, filter_status)"
            )
        if not has_tags:
            conn.exec_driver_sql("ALTER TABLE jobposting ADD COLUMN tags JSON")
        conn.commit()


def _ensure_resume_owner() -> None:
    """Give every resume an owner profile (multi-profile part 3).

    One-time, gated on ``resume.profile_id``. A profile owns the resume it uses;
    when two profiles used the same upload, the active one (then the oldest)
    keeps it and each other gets its own copy of the row (same PDF), with its
    scores re-pointed at the copy so they don't read as stale. Uploads nobody
    uses go to the profile whose scores were computed against them, and anything
    left to the active profile.
    """
    with engine().connect() as conn:
        if "profile_id" in _table_cols(conn, "resume"):
            return
        _begin(conn)
        conn.exec_driver_sql("ALTER TABLE resume ADD COLUMN profile_id INTEGER")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_resume_profile_id ON resume (profile_id)"
        )
        copied = ", ".join(
            c.name
            for c in SQLModel.metadata.tables["resume"].columns
            if c.name not in ("id", "profile_id", "is_active")
        )
        users = conn.exec_driver_sql(
            "SELECT id, resume_id FROM searchprofile WHERE resume_id IS NOT NULL "
            "ORDER BY is_active DESC, id"
        ).all()
        for pid, rid in users:
            row = conn.exec_driver_sql(
                "SELECT profile_id FROM resume WHERE id = ?", (rid,)
            ).first()
            if row is None:
                continue  # dangling; activate_profile repairs it
            if row[0] is None:
                conn.exec_driver_sql(
                    "UPDATE resume SET profile_id = ? WHERE id = ?", (pid, rid)
                )
                continue
            new_id = conn.exec_driver_sql(
                f"INSERT INTO resume ({copied}, is_active, profile_id) "
                f"SELECT {copied}, 0, ? FROM resume WHERE id = ?",
                (pid, rid),
            ).lastrowid
            conn.exec_driver_sql(
                "UPDATE searchprofile SET resume_id = ? WHERE id = ?", (new_id, pid)
            )
            for table in ("matchscore", "matchscorehistory"):
                conn.exec_driver_sql(
                    f"UPDATE {table} SET resume_id = ? WHERE resume_id = ? AND profile_id = ?",
                    (new_id, rid, pid),
                )
        conn.exec_driver_sql(
            "UPDATE resume SET profile_id = ("
            "  SELECT profile_id FROM ("
            "    SELECT resume_id, profile_id FROM matchscorehistory"
            "    UNION ALL SELECT resume_id, profile_id FROM matchscore"
            "  ) s WHERE s.resume_id = resume.id"
            "  GROUP BY profile_id ORDER BY COUNT(*) DESC LIMIT 1"
            ") WHERE profile_id IS NULL"
        )
        if conn.exec_driver_sql("SELECT 1 FROM resume WHERE profile_id IS NULL").first():
            pid = _active_profile_id_from(conn)
            if pid is None:
                pid = _insert_default_profile(conn)
            conn.exec_driver_sql(
                "UPDATE resume SET profile_id = ? WHERE profile_id IS NULL", (pid,)
            )
        conn.commit()
