from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JOB_APPLIER_", extra="ignore")

    db_path: Path = REPO_ROOT / "data" / "jobs.db"
    resumes_dir: Path = REPO_ROOT / "data" / "resumes"
    applications_dir: Path = REPO_ROOT / "applications"
    max_resume_bytes: int = 10 * 1024 * 1024  # 10 MiB

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "http://localhost:5174"


settings = Settings()
