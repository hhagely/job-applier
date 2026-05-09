# CLAUDE.md

Personal job-board project. The README has the full architecture and daily flow — this file is for conventions and gotchas specific to working in the codebase with Claude Code.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLModel (SQLite), `uv` for deps, `typer` CLI exposed as `job-applier`.
- **Frontend**: SvelteKit (in [web/](web/)), runs on `:5174`. The user is learning SvelteKit on this project — prefer idiomatic SvelteKit patterns (form actions, `+page.server.ts` loaders) over client-side fetch.
- **LLM**: never called server-side. Scoring + drafting happen via slash commands in [.claude/commands/](.claude/commands/) that run inside the user's Claude Code session. Do not add `anthropic` SDK calls or API key handling.

## Running things

| Task | Command |
| --- | --- |
| Install deps | `make setup` |
| Backend (`:8000`) | `make api` |
| Frontend (`:5174`) | `make web` |
| Pull jobs | `make ingest` |
| Refresh ATS slug list | `make refresh-slugs` (or `make refresh-slugs-full` to also re-verify existing) |
| Lint | `make lint` (ruff) |
| Init DB | `uv run job-applier init` (creates tables + seeds slugs from `companies.py` if empty) |

Always run Python commands through `uv run` — there's no activated venv assumption.

## Layout pointers

- [src/job_applier/api/](src/job_applier/api/) — FastAPI app + Pydantic schemas. The frontend talks to this via [web/src/lib/api.ts](web/src/lib/api.ts), which is browser-safe (no `$env/dynamic/private` imports).
- [src/job_applier/sources/](src/job_applier/sources/) — source adapters. Add one by implementing the `SourceAdapter` protocol in [base.py](src/job_applier/sources/base.py) and registering in [__init__.py](src/job_applier/sources/__init__.py). Greenhouse + Lever slugs come from the `SourceSlug` DB table at runtime (not from `companies.py` — that file is just the seed for fresh installs). Use `job-applier refresh-slugs` to expand the list from the SimplifyJobs community feed; see [src/job_applier/sources/refresh.py](src/job_applier/sources/refresh.py).
- [src/job_applier/filters/rules.py](src/job_applier/filters/rules.py) — the hard filter. Jobs that fail the filter are not persisted; only `passed` and `manual` postings are written. Dedupe is therefore best-effort for dropped jobs — they get re-evaluated on each ingest, which is fine because `evaluate()` is cheap regex.
- [src/job_applier/config.py](src/job_applier/config.py) — paths and ports. Settings use `JOB_APPLIER_` env prefix.
- [.claude/commands/match-pending.md](.claude/commands/match-pending.md) — the scoring slash command. Source of truth for the scoring contract.

## Conventions

- **Status changes go through SvelteKit form actions**, not client `fetch`. Keep new mutations server-side in `+page.server.ts`.
- **No LinkedIn/Indeed scraping** — ToS violation. Stick to open ATS endpoints and aggregator APIs.
- **No automated company-ethics scoring** — the user maintains a manual blocklist; LLM ethics judgments are unreliable.
- **Markdown is the master format for resumes**, PDF is what gets uploaded for actual submission. Per-job tailoring happens in markdown.
- **Generated artifacts** ([applications/](applications/), [data/](data/)) are gitignored — don't commit them.

## Things that have already been decided

- Sources to add next: Greenhouse, Lever, Ashby, Adzuna, USAJobs.
- Forthcoming slash command: `/draft <job-id>` for cover-letter + tailored-resume drafts.
- Jobs DB lives at [data/jobs.db](data/jobs.db); resume PDFs at [data/resumes/](data/resumes/).
