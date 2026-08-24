from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from job_applier.contracts import MAX_GHOSTED_AFTER_DAYS, MIN_GHOSTED_AFTER_DAYS
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


class WatchedCompanyOut(BaseModel):
    """One hand-added company job board (a ``SourceSlug`` row the user created)."""

    id: int
    source: str
    slug: str
    label: str
    enabled: bool
    last_job_count: Optional[int] = None
    last_error: Optional[str] = None
    added_at: datetime


class WatchedCompanyAddIn(BaseModel):
    # A company name ("Stripe") or a pasted job-board URL — the server decides
    # which it got.
    query: str


class WatchedCompanyAddOut(BaseModel):
    status: Literal["added", "already_searched"]
    message: str
    companies: list[WatchedCompanyOut]


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


class StatusFacet(str, Enum):
    """A queue status facet: every ``ApplicationStatus``, plus ``none``.

    The queue filters on "unset" as if it were a status, but a posting the user
    has never triaged has *no* ``Application`` row at all — so the facet set is
    the enum plus one synthetic member. Mirrors ``StatusFilter`` / ``jobStatusKey``
    in ``web/src/lib/queueFilters.ts``; drift between the two is caught by
    ``tests/test_status_contract.py``.
    """

    new = "new"
    interested = "interested"
    drafted = "drafted"
    applied = "applied"
    screening = "screening"
    interviewing = "interviewing"
    rejected = "rejected"
    no_response = "no_response"
    archived = "archived"
    none = "none"


class StatusCountsOut(BaseModel):
    """Per-facet totals for the *whole* queue, not just a fetched page.

    The queue's chips are counts of everything matching, so they cannot be
    derived from the rows ``/api/jobs`` returned under a limit — a chip reading
    "applied 4" next to a list of 52 is the bug this exists to prevent.
    """

    counts: dict[StatusFacet, int]
    total: int


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
    # Canonical full name of the user's state of residence (e.g. "Missouri"), or
    # None/"" to leave the state-allow-list rule off. Validated + normalized on the
    # PUT path. Used only for ingest filtering.
    home_state: Optional[str] = None


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


class ModelOptionOut(BaseModel):
    value: str
    label: str


class ProviderOut(BaseModel):
    name: str
    display_name: str
    tier: Literal["recommended", "best-effort"]
    available: bool
    version: Optional[str] = None
    # Baseline-scoring model choices for *this* provider, so the Settings dropdown
    # can re-populate from the radio selection without waiting for a save. Empty
    # means the UI should offer a free-text field instead.
    scoring_models: list[ModelOptionOut] = []
    scoring_model_default: Optional[str] = None


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


class CompanyCoverageOut(BaseModel):
    """How many company job boards ingest currently watches, and when that list
    was last checked for new ones. Drives the "Companies searched" card on
    /search — the staleness of this list is otherwise invisible."""

    total: int
    enabled: int
    by_source: dict[str, int]
    # When the discovery pass last RAN (not when a row last changed): a run that
    # finds nothing new still counts as "we looked". None until the first run.
    last_checked_at: Optional[datetime] = None


class PreferencesOut(BaseModel):
    """User-tunable app preferences (the ``AppSetting`` key/value rows).

    One object rather than an endpoint per key, so the next preference is a field
    here instead of another route.
    """

    ghosted_after_days: int


class PreferencesUpdate(BaseModel):
    """Partial update: an omitted field is left at its stored value.

    The bounds are enforced here rather than in the UI so a hand-rolled PATCH
    can't park a nonsense value in the key/value table.
    """

    ghosted_after_days: Optional[int] = Field(
        default=None,
        ge=MIN_GHOSTED_AFTER_DAYS,
        le=MAX_GHOSTED_AFTER_DAYS,
    )


class TaskOut(BaseModel):
    id: str
    kind: str
    total: int
    done: int
    status: Literal["running", "done", "error"]
    errors: list[str] = []
    results: list[str] = []
    ref: str | None = None
