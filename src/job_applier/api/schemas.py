from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from job_applier.models.db import ApplicationStatus, FilterStatus


class CompanyOut(BaseModel):
    id: int
    name: str
    domain: Optional[str] = None
    is_blocked: bool
    notes: Optional[str] = None


class ScoreOut(BaseModel):
    score: int
    rubric: dict
    reasoning: Optional[str]
    scored_by: str
    scored_at: datetime
    resume_id: Optional[int] = None
    resume_filename: Optional[str] = None
    score_kind: str = "baseline"


class ApplicationOut(BaseModel):
    status: ApplicationStatus
    notes: Optional[str]
    applied_at: Optional[datetime]
    updated_at: datetime
    next_followup_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    outcome: Optional[str] = None


class JobOut(BaseModel):
    id: int
    source: str
    url: str
    title: str
    location: Optional[str]
    remote: bool
    employment_type: Optional[str]
    posted_at: Optional[datetime]
    ingested_at: datetime
    filter_status: FilterStatus
    filter_reason: Optional[str]
    company: Optional[CompanyOut]
    score: Optional[ScoreOut]
    application: Optional[ApplicationOut]


class JobDetail(JobOut):
    description: str


class StatusUpdate(BaseModel):
    status: ApplicationStatus
    notes: Optional[str] = None
    next_followup_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    outcome: Optional[str] = None


class BulkStatusUpdate(BaseModel):
    job_ids: list[int]
    status: ApplicationStatus
    next_followup_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    outcome: Optional[str] = None


class NotesUpdate(BaseModel):
    notes: str


class FollowupUpdate(BaseModel):
    next_followup_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    outcome: Optional[str] = None


class ScoreIn(BaseModel):
    score: int
    rubric: dict = {}
    reasoning: Optional[str] = None
    scored_by: str = "claude-code"
    score_kind: Literal["baseline", "tailored"] = "baseline"


class PendingMatchJob(BaseModel):
    id: int
    title: str
    company_name: str
    url: str
    location: Optional[str]
    description: str


class ResumeOut(BaseModel):
    id: int
    original_filename: str
    page_count: Optional[int]
    is_active: bool
    uploaded_at: datetime
    extracted_text: str


class DraftIn(BaseModel):
    resume_md: Optional[str] = None
    cover_letter_md: Optional[str] = None


class DraftOut(BaseModel):
    job_id: int
    has_resume_md: bool
    has_resume_pdf: bool
    has_cover_letter_md: bool
    has_cover_letter_pdf: bool
    updated_at: Optional[datetime]
    resume_md: Optional[str] = None
    cover_letter_md: Optional[str] = None
