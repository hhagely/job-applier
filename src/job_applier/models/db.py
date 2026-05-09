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

    job: Optional[JobPosting] = Relationship(back_populates="score")


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobposting.id", unique=True)

    status: ApplicationStatus = ApplicationStatus.new
    notes: Optional[str] = None
    applied_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=_utcnow)

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


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
