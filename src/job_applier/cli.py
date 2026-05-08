from __future__ import annotations

import json

import typer

from job_applier.config import settings
from job_applier.ingest import run_ingest
from job_applier.models import create_db_and_tables

app = typer.Typer(no_args_is_help=True, help="job-applier CLI")


@app.command()
def init() -> None:
    """Create the SQLite database and tables."""
    create_db_and_tables()
    typer.echo(f"DB ready at {settings.db_path}")


@app.command()
def ingest() -> None:
    """Pull jobs from configured sources, dedupe, filter, persist."""
    create_db_and_tables()
    stats = run_ingest()
    typer.echo(json.dumps(stats.__dict__, indent=2))


@app.command()
def serve(host: str | None = None, port: int | None = None) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run(
        "job_applier.api.app:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=True,
    )


if __name__ == "__main__":
    app()
