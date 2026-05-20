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
| Prune old postings | `make prune` (clears description/raw on old/archived rows; keeps dedupe hashes) |
| Backfill JD SimHashes | `make dedupe-jd` (idempotent; soft-links near-duplicate JDs) |
| Lint | `make lint` (ruff) |
| Backend tests | `make test-api` (pytest) |
| Frontend tests | `make test-web` (vitest) |
| All tests | `make test` |
| Init DB | `uv run job-applier init` (creates tables, runs lightweight `ALTER TABLE` migrations, seeds slugs from `companies.py` if empty) |

Always run Python commands through `uv run` — there's no activated venv assumption.

## Layout pointers

- [src/job_applier/api/](src/job_applier/api/) — FastAPI app + Pydantic schemas. The frontend talks to this via [web/src/lib/api.ts](web/src/lib/api.ts), which is browser-safe (no `$env/dynamic/private` imports).
- [src/job_applier/sources/](src/job_applier/sources/) — source adapters. Add one by implementing the `SourceAdapter` protocol in [base.py](src/job_applier/sources/base.py) and registering in [__init__.py](src/job_applier/sources/__init__.py). Per-company sources (Greenhouse, Lever, Ashby, Workday, Workable, SmartRecruiters, Jibe) read slugs from the `SourceSlug` DB table at runtime; aggregators (RemoteOK, We Work Remotely, Hacker News, Y Combinator) are config-free. `companies.py` is just the seed for fresh installs and is now per-source — adding a new source type later picks up its seed on the next `init`. Workday slugs use the packed format `{tenant}|{region}|{site}` since a tenant alone isn't enough to construct the URL. SmartRecruiters slugs are case-sensitive (`Visa` ≠ `visa`); Jibe slugs are the jibeapply.com subdomain (e.g. `githubinc`); the others are lowercased.
- [src/job_applier/filters/rules.py](src/job_applier/filters/rules.py) — the hard filter. The location / remote / sales-title / Missouri-allow-list rules are fixed; seniority + required/excluded tech are loaded from the `SearchProfile` row via `load_active_config()` and edited at `/search`. Jobs that fail the role criteria are not persisted; only `passed` and `manual` postings are written. Dedupe is therefore best-effort for dropped jobs — they get re-evaluated on each ingest, which is fine because `evaluate()` is cheap regex.
- [src/job_applier/drafts.py](src/job_applier/drafts.py) — tailored draft persistence + weasyprint PDF rendering. `/draft` writes markdown via the API; the backend renders the PDFs. The user can edit the markdown and click "Re-render PDFs from markdown" in the UI.
- [src/job_applier/models/db.py](src/job_applier/models/db.py) — schema. Migrations are `_ensure_*_columns()` helpers run on every startup (idempotent `PRAGMA table_info` + `ALTER TABLE`). When you add a column, add a matching helper here, don't introduce alembic.
- [src/job_applier/config.py](src/job_applier/config.py) — paths and ports. Settings use `JOB_APPLIER_` env prefix.
- [.claude/commands/](.claude/commands/) — four slash commands:
  - `match-pending.md` — source of truth for the scoring rubric.
  - `score-draft.md` — same rubric, applied to a tailored resume; must stay in sync with `match-pending.md`.
  - `draft.md` — tailored resume + cover letter. Heavy ATS-formatting rules; chain-calls `/score-draft` after each save.
  - `suggest-roles.md` — proposes a `SearchProfile`, writes to `recommendations_draft` only; never mutates the live filter.
- [web/src/lib/draftCart.svelte.ts](web/src/lib/draftCart.svelte.ts) — Svelte rune-based store for the cross-route draft cart. Persisted in `localStorage`. Used from `/`, `/jobs/[id]`, `/followups`.

## Conventions

- **Status changes go through SvelteKit form actions**, not client `fetch`. Keep new mutations server-side in `+page.server.ts`.
- **No LinkedIn/Indeed scraping** — ToS violation. Stick to open ATS endpoints and aggregator APIs.
- **No automated company-ethics scoring** — the user maintains a manual blocklist; LLM ethics judgments are unreliable.
- **Markdown is the master format for resumes**, PDF is what gets uploaded for actual submission. Per-job tailoring happens in markdown.
- **Generated artifacts** ([applications/](applications/), [data/](data/)) are gitignored — don't commit them.
- **No em dashes / smart quotes in generated resume + cover-letter markdown.** ATS-fingerprint signal. The full ban list is in [.claude/commands/draft.md](.claude/commands/draft.md). This rule applies to drafts only — feel free to use em dashes elsewhere.
- **Score history**: scoring writes both the active `MatchScore` and a `MatchScoreHistory` row so re-scoring (resume changed, tailored draft saved) preserves the previous read. `score_kind` is `baseline` or `tailored`.

## Things that have already been decided

- Source adapters in place: Greenhouse, Lever, Ashby, Workday, Workable, SmartRecruiters, Jibe (iCIMS careers overlay), RemoteOK, We Work Remotely, Hacker News "Who is hiring", and Y Combinator (via HN `jobstories` + JSON-LD on `ycombinator.com` job pages). Adzuna and Remotive were removed — Adzuna's API is hirer-targeted, Remotive's listings don't change day-to-day.
- `make diagnose-filter` dry-runs every source and reports filter outcomes without persisting anything. Use when ingest counts feel low to tell whether the bottleneck is sourcing or filtering.
- Cross-source dedupe runs on `(normalized_company, normalized_title)` so the same role from multiple sources collapses to one DB row. See `cross_source_hash` in [src/job_applier/ingest.py](src/job_applier/ingest.py). A third layer — 64-bit JD SimHash — catches near-duplicate descriptions and soft-links them via `JobPosting.duplicate_of`.
- The hard filter is configurable per `SearchProfile`. `/suggest-roles` proposes a profile from the resume but never mutates the live one — it writes to `recommendations_draft`; the user accepts via the `/search` UI.
- Application statuses: `new`, `interested`, `drafted`, `applied`, `screening`, `interviewing`, `rejected`, `archived`. Follow-up tracking lives in `Application.next_followup_at` / `last_contact_at` and surfaces at `/followups`.
- Scores are versioned: each `POST /api/jobs/{id}/score` overwrites the active row and snapshots the prior value to `MatchScoreHistory`. When the active resume changes, prior scores are flagged stale and re-included in `/api/pending-match`.
- Jobs DB lives at [data/jobs.db](data/jobs.db); resume PDFs at [data/resumes/](data/resumes/); tailored drafts at `applications/<id>/{resume,cover_letter}.{md,pdf}`. All three paths are gitignored.
