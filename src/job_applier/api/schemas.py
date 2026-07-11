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


class BlacklistedCompanyOut(BaseModel):
    id: int
    name: str
    normalized_name: str
    reason: Optional[str] = None
    created_at: datetime


class BlacklistAddIn(BaseModel):
    name: str
    reason: Optional[str] = None


class ScoreOut(BaseModel):
    score: int
    rubric: dict
    reasoning: Optional[str]
    scored_by: str
    scored_at: datetime
    resume_id: Optional[int] = None
    resume_filename: Optional[str] = None
    score_kind: Literal["baseline", "tailored"] = "baseline"
    is_stale: bool = False


class ApplicationOut(BaseModel):
    status: ApplicationStatus
    notes: Optional[str]
    applied_at: Optional[datetime]
    updated_at: datetime
    next_followup_at: Optional[datetime] = None
    last_contact_at: Optional[datetime] = None
    outcome: Optional[str] = None
    used_for_unemployment: bool = False
    used_for_unemployment_at: Optional[datetime] = None


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
    duplicate_of: Optional[int] = None


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


class UnemploymentUpdate(BaseModel):
    used: bool


class BulkUnemploymentUpdate(BaseModel):
    job_ids: list[int]
    used: bool


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


class SearchProfileBody(BaseModel):
    """Shape used for both reading and writing the active search profile.

    All fields are lists of strings so they round-trip cleanly through the JSON
    columns. Empty lists are legal — the filter falls back to its built-in
    defaults when ``required_tech`` or ``seniority_terms`` is empty.
    """

    role_titles: list[str] = []
    seniority_terms: list[str] = []
    required_tech: list[str] = []
    excluded_tech: list[str] = []
    extracted_skills: list[str] = []


class SearchProfileOut(SearchProfileBody):
    id: Optional[int] = None
    recommendations_draft: Optional[dict] = None
    updated_at: Optional[datetime] = None
    using_defaults: bool = False  # True when the filter is falling back


class SearchProfileRecommendationIn(BaseModel):
    """Payload posted by the /suggest-roles slash command after analyzing the
    resume. The shape mirrors ``SearchProfileBody`` plus a free-form rationale
    sentence the UI shows alongside the accept/reject buttons.
    """

    role_titles: list[str] = []
    seniority_terms: list[str] = []
    required_tech: list[str] = []
    excluded_tech: list[str] = []
    extracted_skills: list[str] = []
    rationale: Optional[str] = None


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


class ProviderOut(BaseModel):
    name: str
    display_name: str
    tier: Literal["recommended", "best-effort"]
    available: bool
    version: Optional[str] = None


class ProvidersOut(BaseModel):
    providers: list[ProviderOut]
    selected: Optional[str] = None
    model: Optional[str] = None
    # Baseline (bulk) scoring model: the persisted override (may be None) and the
    # selected provider's built-in default, shown as the input placeholder.
    scoring_model: Optional[str] = None
    scoring_model_default: Optional[str] = None


class SelectProviderIn(BaseModel):
    name: str
    model: Optional[str] = None
    scoring_model: Optional[str] = None


class AiTestIn(BaseModel):
    prompt: Optional[str] = None


class AiTestOut(BaseModel):
    ok: bool
    output: Optional[str] = None
    error: Optional[str] = None


class ScorePendingIn(BaseModel):
    job_ids: Optional[list[int]] = None
    include_stale: bool = True


class DraftBatchIn(BaseModel):
    job_ids: list[int]


class StartTaskOut(BaseModel):
    task_id: str


class TaskOut(BaseModel):
    id: str
    kind: str
    total: int
    done: int
    status: Literal["running", "done", "error"]
    errors: list[str] = []
    results: list[str] = []
    ref: str | None = None
