from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Column, UniqueConstraint, event
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine

from job_applier.config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FilterStatus(str, Enum):
    passed = "passed"
    dropped = "dropped"
    manual = "manual"  # ambiguous — surface for human review


class ApplicationStatus(str, Enum):
    new = "new"
    interested = "interested"
    drafted = "drafted"
    applied = "applied"
    screening = "screening"
    interviewing = "interviewing"
    rejected = "rejected"
    archived = "archived"


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    domain: Optional[str] = None
    is_blocked: bool = False
    notes: Optional[str] = None

    jobs: list["JobPosting"] = Relationship(back_populates="company")


class JobPosting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    source: str = Field(index=True)
    source_id: str = Field(index=True)
    url: str
    title: str
    description: str
    location: Optional[str] = None
    remote: bool = True
    employment_type: Optional[str] = None
    posted_at: Optional[datetime] = None
    ingested_at: datetime = Field(default_factory=_utcnow)

    dedupe_hash: str = Field(index=True, unique=True)
    # Cross-source fingerprint: normalized (company, title). Lets the same role
    # surfaced via multiple sources (Greenhouse + aggregator, etc.) collapse to
    # one row. Nullable for backward-compat with rows ingested before this column
    # existed; new inserts always populate it.
    cross_source_hash: Optional[str] = Field(default=None, index=True)
    # 64-bit SimHash over the job description as a 16-char hex string. Used to
    # detect near-duplicate JDs (reposts, aggregator copies with reworded titles)
    # that get past the (source, title) checks above. Null when the description
    # is too short to fingerprint reliably.
    jd_fingerprint: Optional[str] = Field(default=None, index=True)
    # Soft link to the canonical posting when this row was flagged as a JD-similar
    # duplicate. The row is still persisted; the API hides it from the default
    # listing.
    duplicate_of: Optional[int] = Field(
        default=None, foreign_key="jobposting.id", index=True
    )
    raw: dict = Field(default_factory=dict, sa_column=Column(JSON))

    filter_status: FilterStatus = FilterStatus.passed
    filter_reason: Optional[str] = None

    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    company: Optional[Company] = Relationship(back_populates="jobs")

    score: Optional["MatchScore"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )
    application: Optional["Application"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )


class MatchScore(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobposting.id", unique=True)

    score: int  # 0-100
    rubric: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reasoning: Optional[str] = None
    scored_by: str = "claude-code"
    scored_at: datetime = Field(default_factory=_utcnow)
    resume_id: Optional[int] = Field(default=None, foreign_key="resume.id")
    score_kind: str = Field(default="baseline", index=True)

    job: Optional[JobPosting] = Relationship(back_populates="score")


class MatchScoreHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobposting.id", index=True)

    score: int
    rubric: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reasoning: Optional[str] = None
    scored_by: str = "claude-code"
    scored_at: datetime = Field(default_factory=_utcnow)
    resume_id: Optional[int] = Field(default=None, foreign_key="resume.id")
    score_kind: str = Field(default="baseline", index=True)


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobposting.id", unique=True)

    status: ApplicationStatus = ApplicationStatus.new
    notes: Optional[str] = None
    applied_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=_utcnow)

    next_followup_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    outcome: Optional[str] = None

    # Tracks applications the user has reported to the unemployment office as
    # part of a weekly work-search claim. The timestamp records when it was
    # marked (i.e. roughly which claim week it counted toward).
    used_for_unemployment: bool = Field(default=False, index=True)
    used_for_unemployment_at: Optional[datetime] = None

    job: Optional[JobPosting] = Relationship(back_populates="application")


class SourceSlug(SQLModel, table=True):
    """A per-company ATS slug to ingest from (e.g. greenhouse:stripe).

    The DB is the source of truth at runtime; ``sources/companies.py`` is a
    one-time seed used by ``job-applier init`` when the table is empty.
    Run ``job-applier refresh-slugs`` to expand the list from the
    SimplifyJobs community feed.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)  # "greenhouse" | "lever"
    slug: str = Field(index=True)
    enabled: bool = Field(default=True, index=True)
    last_fetched_at: Optional[datetime] = None
    last_job_count: Optional[int] = None
    last_error: Optional[str] = None
    added_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (UniqueConstraint("source", "slug", name="uq_sourceslug_source_slug"),)


class Resume(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    original_filename: str
    pdf_path: str  # absolute path under settings.resumes_dir
    extracted_text: str
    page_count: Optional[int] = None
    is_active: bool = Field(default=False, index=True)
    uploaded_at: datetime = Field(default_factory=_utcnow)


class SearchProfile(SQLModel, table=True):
    """User's configured job-search criteria. Singleton (one active row).

    Drives the hard filter at ingest time. When empty, the filter falls back to
    its built-in defaults so a fresh install still works.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    # Human-readable role titles the user wants surfaced
    # (e.g. ["Senior Software Engineer", "Staff Backend Engineer"]).
    role_titles: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Seniority terms that gate the title regex
    # (e.g. ["senior", "staff", "principal", "lead", "architect"]).
    seniority_terms: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Tech/skills the posting MUST reference (any-of). Filter drops if none match.
    required_tech: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Tech that disqualifies a posting when it's the primary stack (e.g. "angular").
    excluded_tech: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Canonical full name of the user's state of residence (e.g. "Missouri"), or
    # None. Drives the state-allow-list rule: a posting that can "only hire in
    # X, Y, Z" is dropped when the home state isn't in that list. Null skips the
    # rule entirely (no state assumption). Used ONLY for ingest filtering — never
    # for any other purpose. Edited at /search.
    home_state: Optional[str] = None
    # Reference: skills extracted from the user's resume by the LLM. Not used by
    # the filter directly — surfaced in the UI so the user can see what informed
    # the recommendations.
    extracted_skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Pending LLM-generated proposal awaiting user accept/reject. Shape mirrors
    # the active fields (role_titles/seniority_terms/required_tech/excluded_tech
    # /extracted_skills) plus a free-form "rationale" string. Null when no draft.
    recommendations_draft: Optional[dict] = Field(
        default=None, sa_column=Column(JSON)
    )
    updated_at: datetime = Field(default_factory=_utcnow)


class AppSetting(SQLModel, table=True):
    """Tiny key/value store for app-level settings (e.g. selected AI provider).

    A dedicated table rather than overloading SearchProfile. Brand-new table, so
    ``create_all`` handles it with no ALTER — additive and safe for `main`.
    """

    key: str = Field(primary_key=True)
    value: str


class BlacklistedCompany(SQLModel, table=True):
    """A company the user never wants surfaced. Matched at ingest against the
    normalized company name, so a job from a blacklisted employer is dropped
    before it's persisted — even the first time we see that company (no
    ``Company`` row needs to exist yet).

    ``normalized_name`` is produced by ``ingest.normalize_company`` — the SAME
    normalizer used for cross-source dedupe — so user-typed variants like
    "Meta", "Meta Inc", and "Meta, Inc." all collapse to one key and match
    however a source spells the employer. ``name`` keeps the original spelling
    the user entered for display. Brand-new table, so ``create_all`` handles it
    with no ALTER.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    normalized_name: str = Field(index=True, unique=True)
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


_engine = None


def get_setting(session: "Session", key: str, default: Optional[str] = None) -> Optional[str]:
    row = session.get(AppSetting, key)
    return row.value if row is not None else default


def set_setting(session: "Session", key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def engine():
    global _engine
    if _engine is None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)

        # SQLite defaults to busy_timeout=0 and a rollback journal, so the moment
        # two connections contend for the write lock the loser raises
        # "database is locked" — which surfaced as an opaque HTTP 500. Two flows
        # make this easy to hit: the background scorer/ingest tasks write from
        # their own thread/session, and the synchronous suggest-roles endpoint
        # holds its read transaction open for the ~45s of the LLM call before it
        # commits. WAL lets readers and the single writer coexist, and a busy
        # timeout makes a contending writer wait for the lock instead of erroring.
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")  # ms; wait up to 30s for the lock
            cur.execute("PRAGMA synchronous=NORMAL")  # safe + faster under WAL
            cur.close()

    return _engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine())
    _ensure_cross_source_hash_column()
    _ensure_matchscore_resume_id_column()
    _ensure_score_kind_columns()
    _ensure_application_followup_columns()
    _ensure_application_unemployment_columns()
    _ensure_jd_dedupe_columns()
    _ensure_searchprofile_columns()


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
    """Add SearchProfile.home_state on existing DBs that pre-date the column.

    Nullable with no default: existing profiles migrate to "no home state set",
    which skips the state-allow-list rule until the user picks a state at /search.
    """
    with engine().connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(searchprofile)")}
        if "home_state" not in cols:
            conn.exec_driver_sql("ALTER TABLE searchprofile ADD COLUMN home_state VARCHAR")
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


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
