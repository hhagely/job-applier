from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JOB_APPLIER_", extra="ignore")

    db_path: Path = REPO_ROOT / "data" / "jobs.db"
    resume_path: Path = REPO_ROOT / "resume" / "master.md"
    applications_dir: Path = REPO_ROOT / "applications"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "http://localhost:5174"


settings = Settings()
