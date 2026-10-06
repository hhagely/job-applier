from collections.abc import Iterator
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Column, Index, UniqueConstraint, event
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import with_loader_criteria
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
    # Applied, then silence. Terminal, but deliberately NOT `rejected` (nobody
    # said no, so the rejection count stays honest) and NOT `archived` (that is
    # the machine-owned bucket for postings never pursued).
    no_response = "no_response"
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
    # The source's tags (RawJob.tags). Persisted so a profile added or edited
    # after the scrape can be matched against the stored posting exactly as it
    # would have been at ingest. Null on postings saved before the column.
    tags: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))

    # The *shared* rules' verdict (remote, US, sales, crypto); always ``passed``
    # for anything stored (legacy per-profile verdicts are moved onto the links
    # and reset by the migration). Each profile's own verdict is on its
    # JobProfileLink, and that is what the API reports.
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
    # The current profile's verdict on this posting (profile-scoped, like
    # ``application``), or None when the profile hasn't evaluated it.
    link: Optional["JobProfileLink"] = Relationship(
        sa_relationship_kwargs={"uselist": False, "viewonly": True}
    )


class MatchScore(SQLModel, table=True):
    """A profile's active score for a posting: one per (job, profile).

    Sessions only ever see the current profile's rows (see ``_scope_to_profile``),
    which is what keeps ``JobPosting.score`` a single object.
    """

    __table_args__ = (
        UniqueConstraint("job_id", "profile_id", name="uq_matchscore_job_profile"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobposting.id", index=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)

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
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)

    score: int
    rubric: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reasoning: Optional[str] = None
    scored_by: str = "claude-code"
    scored_at: datetime = Field(default_factory=_utcnow)
    resume_id: Optional[int] = Field(default=None, foreign_key="resume.id")
    score_kind: str = Field(default="baseline", index=True)


class Application(SQLModel, table=True):
    """A profile's tracking state for a posting: one per (job, profile), so two
    people applying to the same job each have their own status, notes, and
    follow-ups. Profile-scoped like ``MatchScore``."""

    __table_args__ = (
        UniqueConstraint("job_id", "profile_id", name="uq_application_job_profile"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobposting.id", index=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)

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

    ``added_by_user`` marks rows the user added by hand at ``/search`` (the
    company whitelist) rather than ones that arrived via the seed or feed
    discovery. Ingest treats every row the same; the flag exists so the UI can
    list back the handful the user is responsible for — the discovered list runs
    to thousands. ``label`` keeps the name they typed for display, since a slug
    like ``acme-corp`` (or a packed Workday ``tenant|region|site``) doesn't read
    as a company name.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(index=True)  # "greenhouse" | "lever"
    slug: str = Field(index=True)
    enabled: bool = Field(default=True, index=True)
    last_fetched_at: Optional[datetime] = None
    last_job_count: Optional[int] = None
    last_error: Optional[str] = None
    added_by_user: bool = Field(default=False, index=True)
    label: Optional[str] = None
    added_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (UniqueConstraint("source", "slug", name="uq_sourceslug_source_slug"),)


class Resume(SQLModel, table=True):
    """An uploaded resume, owned by one profile (profile-scoped like
    ``Application``). A profile can hold several; the one it uses is
    ``SearchProfile.resume_id``. Copying a profile copies the row, not the PDF,
    so two rows may share a ``pdf_path``."""

    id: Optional[int] = Field(default=None, primary_key=True)
    # No foreign key: searchprofile.resume_id already points the other way, and
    # a cycle would cost create_all its table ordering. Stamped like every other
    # profile-scoped row (see ``_stamp_profile``).
    profile_id: int = Field(index=True)
    original_filename: str
    pdf_path: str  # absolute path under settings.resumes_dir
    extracted_text: str
    page_count: Optional[int] = None
    is_active: bool = Field(default=False, index=True)
    uploaded_at: datetime = Field(default_factory=_utcnow)


class SearchProfile(SQLModel, table=True):
    """A saved set of job-search criteria. Many rows, exactly one active.

    The active row drives the hard filter at ingest time. When its lists are
    empty, the filter falls back to built-in defaults so a fresh install still
    works. Switching profiles also switches the active resume — see
    ``services.activate_profile`` for the invariant.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "Default"
    # Exactly one row is active. Readers go through ``services.active_profile``,
    # which falls back to the oldest row if none is flagged.
    is_active: bool = Field(default=False, index=True)
    # The resume scored against and tailored from while this profile is active.
    # Null until the profile is first activated with a resume on file.
    resume_id: Optional[int] = Field(default=None, foreign_key="resume.id")
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


class JobProfileLink(SQLModel, table=True):
    """One profile's verdict on one stored posting.

    The scrape stores every posting that passes the shared rules, once; then
    ``matching`` runs each profile's personal rules (seniority, tech, home-state
    allow-list, blacklist) and records the outcome here — including ``dropped``,
    so a row's existence means "this profile has evaluated this posting" and a
    re-scrape only evaluates what's new. A profile's queue is its ``passed``
    (or ``manual``) rows. Profile-scoped like ``Application``.
    """

    __table_args__ = (
        Index("ix_jobprofilelink_profile_status", "profile_id", "filter_status"),
    )

    job_id: int = Field(foreign_key="jobposting.id", primary_key=True)
    profile_id: int = Field(foreign_key="searchprofile.id", primary_key=True, index=True)
    filter_status: FilterStatus = FilterStatus.passed
    filter_reason: Optional[str] = None
    linked_at: datetime = Field(default_factory=_utcnow)


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
    the user entered for display. Per profile: each person ignores their own
    employers, so uniqueness is on (profile, normalized name).
    """

    __table_args__ = (
        Index(
            "ux_blacklistedcompany_profile_name",
            "profile_id",
            "normalized_name",
            unique=True,
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="searchprofile.id", index=True)
    name: str
    normalized_name: str = Field(index=True)
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


# ---- profile scoping --------------------------------------------------------
#
# Application, MatchScore(+History) and BlacklistedCompany belong to a profile.
# Rather than thread a profile id through every query, every ORM statement a
# Session runs is filtered to one profile (SQLAlchemy's documented multi-tenant
# pattern, ``with_loader_criteria``) and new rows are stamped with it on flush.
# That keeps ``JobPosting.application`` / ``.score`` single objects meaning "the
# current profile's", so the many call sites that read them don't change.
#
# Which profile, in order: ``session.info["profile_id"]`` when a caller pinned
# one; the ``current_profile_id`` ContextVar (background tasks set it, so a run
# keeps writing for the profile that started it even if the user switches); the
# active profile in the DB. Pass ``execution_options(all_profiles=True)`` for the
# rare query that must see every profile.

PROFILE_SCOPED = (
    Application,
    MatchScore,
    MatchScoreHistory,
    BlacklistedCompany,
    JobProfileLink,
    Resume,
)

current_profile_id: ContextVar[Optional[int]] = ContextVar(
    "current_profile_id", default=None
)

_ACTIVE_CACHE = "_active_profile_id"


def _active_profile_id_from(conn) -> Optional[int]:  # noqa: ANN001
    """The active profile on a raw connection (oldest row if none is flagged,
    matching ``profiles.active_profile``). Raw SQL so it can run inside ORM
    events without re-entering them."""
    row = conn.exec_driver_sql(
        "SELECT id FROM searchprofile ORDER BY is_active DESC, id LIMIT 1"
    ).first()
    return row[0] if row else None


def _insert_default_profile(conn) -> int:  # noqa: ANN001
    """Create the active "Default" profile on the active resume, if any. Raw SQL
    so it can run from ORM events and migrations; ``profiles.load_or_create_profile``
    is the ORM path and keeps the same shape."""
    return conn.exec_driver_sql(
        "INSERT INTO searchprofile (name, is_active, resume_id, role_titles, seniority_terms, "
        "required_tech, excluded_tech, extracted_skills, updated_at) "
        "VALUES ('Default', 1, (SELECT id FROM resume WHERE is_active = 1 LIMIT 1), "
        "'[]', '[]', '[]', '[]', '[]', CURRENT_TIMESTAMP)"
    ).lastrowid


def _table_cols(conn, table: str) -> set[str]:  # noqa: ANN001
    return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def _reset_posting_verdicts(conn) -> None:  # noqa: ANN001
    """After legacy verdicts are copied onto the links, leave the posting columns
    meaning only the shared verdict (every stored posting passed it)."""
    conn.exec_driver_sql(
        "UPDATE jobposting SET filter_status = 'passed', filter_reason = NULL "
        "WHERE filter_status IS NOT 'passed' OR filter_reason IS NOT NULL"
    )


def _begin(conn) -> None:  # noqa: ANN001
    """Open a write transaction explicitly so DDL joins it (see the callers)."""
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def session_profile_id(session: Session, *, create: bool = False) -> Optional[int]:
    """The profile this session reads and writes. ``create`` makes a Default
    profile on a fresh install, for the flush that needs somewhere to put a row."""
    pinned = session.info.get("profile_id")
    if pinned is not None:
        return pinned
    ctx = current_profile_id.get()
    if ctx is not None:
        return ctx
    cached = session.info.get(_ACTIVE_CACHE)
    if cached is not None:
        return cached
    conn = session.connection()
    pid = _active_profile_id_from(conn)
    if pid is None and create:
        pid = _insert_default_profile(conn)
    if pid is not None:
        session.info[_ACTIVE_CACHE] = pid
    return pid


def forget_active_profile(session: Session) -> None:
    """Drop the session's cached active profile (after switching profiles)."""
    session.info.pop(_ACTIVE_CACHE, None)


@event.listens_for(SASession, "do_orm_execute")
def _scope_to_profile(state) -> None:  # noqa: ANN001
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.execution_options.get("all_profiles"):
        return
    pid = session_profile_id(state.session)
    if pid is None:
        return
    state.statement = state.statement.options(
        *(
            with_loader_criteria(model, lambda cls: cls.profile_id == pid, include_aliases=True)
            for model in PROFILE_SCOPED
        )
    )


@event.listens_for(SASession, "before_flush")
def _stamp_profile(session, _flush_context, _instances) -> None:  # noqa: ANN001
    pending = [
        obj
        for obj in session.new
        if isinstance(obj, PROFILE_SCOPED) and obj.profile_id is None
    ]
    if pending:
        pid = session_profile_id(session, create=True)
        for obj in pending:
            obj.profile_id = pid


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
    # After create_all (it backfills into the new jobprofilelink table).
    _ensure_multi_profile_columns()
    _ensure_sourceslug_columns()
    # Last: rebuilds tables, so every column helper above must have run first.
    _ensure_per_profile_state()
    _ensure_match_columns()
    _ensure_resume_owner()


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
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(searchprofile)")}
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

        def cols(table: str) -> set[str]:
            return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")}

        todo = [
            t
            for t in ("application", "matchscore", "matchscorehistory", "blacklistedcompany")
            if "profile_id" not in cols(t)
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
                _rebuild_with_profile(conn, table, pid, cols(table))
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


def _rebuild_with_profile(conn, table: str, pid: Optional[int], old_cols: set[str]) -> None:  # noqa: ANN001
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


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
