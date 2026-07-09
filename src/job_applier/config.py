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

    # db_path / resumes_dir default to None and are derived from data_dir below. An
    # explicit env override (JOB_APPLIER_DB_PATH / JOB_APPLIER_RESUMES_DIR) still wins.
    db_path: Path | None = None
    resumes_dir: Path | None = None
    applications_dir: Path = REPO_ROOT / "applications"
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

    @model_validator(mode="after")
    def _derive_data_paths(self) -> "Settings":
        # Derive per-artifact paths from data_dir unless the caller pinned them
        # explicitly, so a single JOB_APPLIER_DATA_DIR relocates everything.
        if self.db_path is None:
            self.db_path = self.data_dir / "jobs.db"
        if self.resumes_dir is None:
            self.resumes_dir = self.data_dir / "resumes"
        return self


settings = Settings()
