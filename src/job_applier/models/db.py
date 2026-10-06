from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Column, Index, UniqueConstraint, event
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

    # Legacy: always ``passed`` (only postings that pass the shared rules are
    # stored, and the migration moved old per-profile verdicts onto the links).
    # Nothing writes or reads these; a profile's verdict is
    # ``JobProfileLink.filter_status``. Kept so existing DBs and old rows load.
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
    # Mirror of the active profile's in-use resume, written only by
    # ``profiles.set_active_resume``. The app reads ``SearchProfile.resume_id``
    # (via ``profiles.active_resume``); this stays for the legacy slash commands.
    is_active: bool = Field(default=False, index=True)
    uploaded_at: datetime = Field(default_factory=_utcnow)


class SearchProfile(SQLModel, table=True):
    """A saved set of job-search criteria. Many rows, exactly one active.

    Every profile's own rules run at match time (``matching``) over the shared
    postings; the active one is the profile the UI shows. When its lists are
    empty, the filter falls back to built-in defaults so a fresh install still
    works. Lifecycle (activate, switch resume) is in ``profiles``.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "Default"
    # Exactly one row is active. Readers go through ``profiles.active_profile``,
    # which falls back to the oldest row if none is flagged.
    is_active: bool = Field(default=False, index=True)
    # The profile's in-use resume (one of its own); null until it has one.
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
    from job_applier.models import migrations  # imports this module

    migrations.run()


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
