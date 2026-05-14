from __future__ import annotations

import json

import typer

from job_applier.config import settings
from job_applier.ingest import (
    archive_existing_duplicates,
    backfill_cross_source_hash,
    dedupe_jd_backfill,
    prune_old_postings,
    run_ingest,
)
from job_applier.models import create_db_and_tables, engine
from job_applier.sources.refresh import refresh_slugs, seed_if_empty
from sqlmodel import Session

app = typer.Typer(no_args_is_help=True, help="job-applier CLI")


@app.command()
def init() -> None:
    """Create the SQLite database and tables, seed slugs if empty."""
    create_db_and_tables()
    seeded = seed_if_empty()
    backfilled = backfill_cross_source_hash()
    typer.echo(f"DB ready at {settings.db_path}")
    if seeded:
        typer.echo(f"Seeded {seeded} source slugs from companies.py")
    if backfilled:
        typer.echo(f"Backfilled cross_source_hash on {backfilled} existing postings")


@app.command()
def ingest() -> None:
    """Pull jobs from configured sources, dedupe, filter, persist."""
    create_db_and_tables()
    stats = run_ingest()
    typer.echo(json.dumps(stats.__dict__, indent=2))


@app.command("refresh-slugs")
def refresh_slugs_cmd(
    reverify: bool = typer.Option(
        False,
        "--reverify",
        help="Also re-check existing slugs and disable any that no longer respond.",
    ),
) -> None:
    """Discover new Greenhouse/Lever slugs from the SimplifyJobs feed and verify them."""
    create_db_and_tables()
    stats = refresh_slugs(reverify_existing=reverify)
    typer.echo(json.dumps(stats.__dict__, indent=2))


@app.command("dedupe-existing")
def dedupe_existing_cmd() -> None:
    """Archive postings that share source + company + normalized title with an
    earlier posting. One-shot cleanup for postings ingested before content-level
    dedupe was added."""
    create_db_and_tables()
    with Session(engine()) as session:
        n = archive_existing_duplicates(session)
    typer.echo(f"Archived {n} duplicate postings")


@app.command("dedupe-jd")
def dedupe_jd_cmd() -> None:
    """Backfill JD SimHash fingerprints and soft-link near-duplicate postings.

    Idempotent: rows that already have a fingerprint or are already flagged as
    duplicates are skipped.
    """
    create_db_and_tables()
    stats = dedupe_jd_backfill()
    typer.echo(json.dumps(stats.__dict__, indent=2))


@app.command()
def prune() -> None:
    """Clear description + raw on archived/rejected, old, or untouched postings.
    Dedupe hashes are preserved so future ingests still skip these as dupes."""
    create_db_and_tables()
    with Session(engine()) as session:
        stats = prune_old_postings(session)
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
