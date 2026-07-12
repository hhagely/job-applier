from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JOB_APPLIER_", extra="ignore")

    # Base data dir. Overriding just this (JOB_APPLIER_DATA_DIR) relocates the DB and
    # resumes together, which is how dev/branch work runs against a copy of the live
    # data without touching it. A packaged app points this at a per-OS user-data dir.
    data_dir: Path = REPO_ROOT / "data"

    # db_path / resumes_dir / applications_dir default to None and are derived from
    # data_dir below. An explicit env override (JOB_APPLIER_DB_PATH /
    # JOB_APPLIER_RESUMES_DIR / JOB_APPLIER_APPLICATIONS_DIR) still wins.
    db_path: Path | None = None
    resumes_dir: Path | None = None
    applications_dir: Path | None = None
    max_resume_bytes: int = 10 * 1024 * 1024  # 10 MiB

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "http://localhost:5174"

    followup_default_days: int = 7

    # Per-call ceiling for the AI CLI when generating a tailored draft. Drafting a
    # full resume + cover letter is a large generation and routinely runs past the
    # 120s provider default, so it gets its own, roomier budget. Tune via
    # JOB_APPLIER_AI_DRAFT_TIMEOUT (seconds) if your provider/model is slower.
    ai_draft_timeout: float = 300

    # Per-call ceiling for a *batch* scoring invocation (the bulk pending-scorer packs
    # several jobs into one call to amortize the resume + rubric prefix). One call does
    # N jobs' worth of work, so it needs more headroom than the 120s single-job default.
    # Tune via JOB_APPLIER_AI_SCORE_BATCH_TIMEOUT (seconds).
    ai_score_batch_timeout: float = 300

    @model_validator(mode="after")
    def _derive_data_paths(self) -> "Settings":
        # Derive per-artifact paths from data_dir unless the caller pinned them
        # explicitly, so a single JOB_APPLIER_DATA_DIR relocates everything.
        if self.db_path is None:
            self.db_path = self.data_dir / "jobs.db"
        if self.resumes_dir is None:
            self.resumes_dir = self.data_dir / "resumes"
        if self.applications_dir is None:
            # Back-compat special case: with the repo-default data_dir, keep drafts
            # at the historical REPO_ROOT/applications so the author's existing local
            # drafts aren't orphaned. Any relocated data_dir (dev copy or the
            # packaged app's user-data dir) nests applications under it, so a
            # distributed install writes tailored drafts to user-data, not next to
            # the read-only install resources.
            if self.data_dir == REPO_ROOT / "data":
                self.applications_dir = REPO_ROOT / "applications"
            else:
                self.applications_dir = self.data_dir / "applications"
        return self


settings = Settings()
