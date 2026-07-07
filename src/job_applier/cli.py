from __future__ import annotations

import json

import typer

from job_applier.config import REPO_ROOT, settings
from job_applier.diagnostics import diagnose_filter, format_diagnostic
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
    """Discover new Greenhouse/Lever/Workable/SmartRecruiters slugs from the SimplifyJobs feed and verify them."""
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


@app.command("diagnose-filter")
def diagnose_filter_cmd(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON instead of the human-readable summary.",
    ),
) -> None:
    """Fetch from every source and report what the hard filter does with each
    job — without writing to the DB. Use when ingest is producing too few
    rows to tell whether sourcing or filtering is the bottleneck."""
    create_db_and_tables()
    diag = diagnose_filter()
    if json_out:
        typer.echo(json.dumps(diag.as_dict(), indent=2))
    else:
        typer.echo(format_diagnostic(diag))


@app.command()
def serve(
    host: str | None = None,
    port: int | None = None,
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Production mode: disable auto-reload (the packaged app / dev launcher uses this).",
    ),
) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run(
        "job_applier.api.app:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=not prod,
    )


def _free_port() -> int:
    """Ask the OS for an unused localhost port (bind-to-0 trick), then release it."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base: str, timeout: float = 30.0) -> bool:
    """Poll ``{base}/api/health`` until it answers or the timeout elapses."""
    import time
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.25)
    return False


@app.command("app-dev")
def app_dev() -> None:
    """Boot the API + built web server on free ports and open the browser.

    Interim launcher that stands in for what Electron's main process will do in a
    later phase: pick free ports, spawn both processes wired together over loopback,
    health-check the backend, open the UI, and tear everything down on exit. Requires
    the web frontend to be built first (``cd web && npm run build``).
    """
    import atexit
    import os
    import signal
    import subprocess
    import sys
    import webbrowser

    web_build = REPO_ROOT / "web" / "build" / "index.js"
    if not web_build.exists():
        typer.secho(
            f"Web build not found at {web_build}. Run `cd web && npm run build` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    api_port = _free_port()
    web_port = _free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    web_url = f"http://127.0.0.1:{web_port}"

    procs: list[subprocess.Popen] = []

    def _shutdown(*_args: object) -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    atexit.register(_shutdown)
    signal.signal(signal.SIGINT, lambda *a: (_shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *a: (_shutdown(), sys.exit(0)))

    api_env = {**os.environ, "JOB_APPLIER_API_PORT": str(api_port)}
    typer.echo(f"Starting API on {api_base} ...")
    procs.append(
        subprocess.Popen(
            [sys.executable, "-m", "job_applier.cli", "serve", "--prod", "--port", str(api_port)],
            env=api_env,
        )
    )

    if not _wait_for_health(api_base):
        typer.secho("API did not become healthy in time.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    web_env = {
        **os.environ,
        "PORT": str(web_port),
        "JOB_APPLIER_API_BASE": api_base,
    }
    typer.echo(f"Starting web server on {web_url} ...")
    procs.append(subprocess.Popen(["node", str(web_build)], env=web_env))

    typer.echo(f"Opening {web_url}")
    webbrowser.open(web_url)

    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    typer.secho("A child process exited; shutting down.", fg=typer.colors.YELLOW)
                    raise typer.Exit(code=1)
            signal.pause()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    app()
