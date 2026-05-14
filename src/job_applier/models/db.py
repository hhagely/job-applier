from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Column, UniqueConstraint
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


_engine = None


def engine():
    global _engine
    if _engine is None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)
    return _engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine())
    _ensure_cross_source_hash_column()
    _ensure_matchscore_resume_id_column()
    _ensure_score_kind_columns()
    _ensure_application_followup_columns()


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
            conn.exec_driver_sql("ALTER TABLE matchscore ADD COLUMN resume_id INTEGER")
            conn.commit()


def _ensure_score_kind_columns() -> None:
    with engine().connect() as conn:
        for table in ("matchscore", "matchscorehistory"):
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if "score_kind" not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN score_kind VARCHAR "
                    "DEFAULT 'baseline'"
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


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
