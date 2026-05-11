# job-applier

A personal job board. Pulls remote roles from open job sources, filters them
against hard rules (Senior+ JS/TS, no Angular), scores survivors against your
resume via Claude Code, and surfaces the results in a SvelteKit review UI so you
can decide which ones are worth tailoring an application for.

No LinkedIn or Indeed scraping — those violate ToS and risk account bans.
Sources are open ATS endpoints (Greenhouse, Lever, Ashby, Workday) and
aggregator feeds (RemoteOK, We Work Remotely, Hacker News "Who is hiring").

## Architecture

```
┌──────────────┐   ingest   ┌──────────┐   filter   ┌──────────┐
│  Source(s)   │ ─────────► │ SQLite   │ ─────────► │ FastAPI  │
│ Greenhouse,… │            │ jobs.db  │            │ :8000    │
└──────────────┘            └──────────┘            └────┬─────┘
                                  ▲                      │ JSON
                                  │ POST /score          ▼
                            ┌─────┴──────┐         ┌──────────┐
                            │ Claude     │ ◄─────► │ SvelteKit│
                            │ Code       │         │ :5174    │
                            │ /match-…   │         └──────────┘
                            └────────────┘
```

The LLM never runs server-side. Scoring, resume tweaks, and cover-letter drafts
all happen by you running slash commands inside Claude Code on this repo, which
reads pending items from the API and posts results back.

## Setup

```sh
make setup                # uv sync + npm install
uv run job-applier init   # create the SQLite DB
```

## Daily flow

1. **Run the API + UI** in two terminals:
   ```sh
   make api    # http://127.0.0.1:8000
   make web    # http://localhost:5174
   ```
2. **Upload your resume** at http://localhost:5174/resume — pick the PDF you
   actually send to employers. The API extracts plain text via `pypdf` and stores
   it as the active resume. Older uploads are kept but inactive.
3. **Ingest** new postings:
   ```sh
   make ingest
   ```
4. **Score the queue** — open Claude Code in this repo and run:
   ```
   /match-pending
   ```
   It pulls the active resume from `/api/resume/current`, fetches unscored jobs,
   and writes scores back. Refresh the UI to see them.
5. **Review** in the UI — change a job's status to `interested`, `applied`,
   `rejected`, etc. Status changes use SvelteKit form actions, so they round-trip
   through the backend without client-side fetch code.

## Project layout

```
src/job_applier/
  api/         # FastAPI app + Pydantic schemas
  filters/     # Hard-rule filter (remote, Senior+, JS/TS, no Angular)
  models/      # SQLModel definitions + DB engine
  sources/     # Source adapters (Greenhouse, Lever, Ashby, Workday, RemoteOK, WWR, HN)
  ingest.py    # Pipeline: fetch → dedupe → filter → persist
  resume_io.py # PDF → text extraction + on-disk storage
  cli.py       # `job-applier` typer CLI
  config.py    # Settings (paths, ports, DB location)
web/           # SvelteKit app
  src/lib/api.ts                                 # typed client used by +page.server.ts
  src/routes/+page.{svelte,server.ts}            # queue
  src/routes/jobs/[id]/+page.{svelte,server.ts}  # detail + status form actions
  src/routes/resume/+page.{svelte,server.ts}     # resume upload + view
.claude/commands/
  match-pending.md   # slash command Claude Code uses to score the queue
applications/        # generated tailored resumes / cover letters per job (gitignored)
data/jobs.db         # SQLite (gitignored)
data/resumes/        # uploaded PDFs (gitignored)
```

## Hard filter rules

Applied at ingest time; failed jobs are kept in the DB but marked `dropped` (auditable):

- **Remote only** — drops `hybrid`, `on-site`, anything mentioning relocation.
- **Senior level** — title must include Senior, Sr., Staff, Principal, Lead, Architect,
  Distinguished, Head of, Director, VP.
- **JS/TS stack** — must reference JavaScript/TypeScript or a JS framework
  (React, Vue, Svelte, Next.js, Node, etc.).
- **No Angular as primary** — Angular in the title disqualifies; Angular in tags
  without another modern framework also disqualifies. Mentions in description
  alongside other frameworks are surfaced as `manual` for you to judge.

Adjust `src/job_applier/filters/rules.py` if your criteria change.

## Sources

| Source           | Config                          | Notes                                                                 |
| ---------------- | ------------------------------- | --------------------------------------------------------------------- |
| Greenhouse       | DB slug list (`SourceSlug`)     | `boards-api.greenhouse.io/v1/boards/{slug}/jobs`                      |
| Lever            | DB slug list (`SourceSlug`)     | `api.lever.co/v0/postings/{slug}`                                     |
| Ashby            | DB slug list (`SourceSlug`)     | `api.ashbyhq.com/posting-api/job-board/{slug}`. Slugs are case-sensitive (`Notion`, not `notion`). |
| Workday          | DB slug list, packed format     | Slug is `{tenant}\|{region}\|{site}` — e.g. `salesforce\|wd12\|External_Career_Site`. List call returns only titles; descriptions need a per-posting detail fetch, so the adapter pre-filters titles before going deep. |
| RemoteOK         | none                            | Single-endpoint aggregator (`remoteok.com/api`).                      |
| We Work Remotely | none                            | Per-category RSS feeds; engineering categories only.                  |
| Hacker News      | none                            | Most recent monthly "Who is hiring" thread, parsed via Algolia HN API. Top-level comments are individual postings. |

### Managing the company slug list

Per-company slugs (Greenhouse / Lever / Ashby / Workday) live in the database
(`SourceSlug` table), not in code. Initial setup seeds the table from
`src/job_applier/sources/companies.py` on first `job-applier init` — and the
seed is per-source, so adding a new source type later picks up its seed on the
next `init` without disturbing the populated tables.

```sh
# Pull new Greenhouse/Lever candidates from the SimplifyJobs feed and verify
make refresh-slugs

# Same, but also re-verify every existing slug across all four per-company
# sources (Greenhouse, Lever, Ashby, Workday) and auto-disable dead boards.
# A Workday tenant returning HTTP 422 is treated as a permanent rejection
# and disabled, since 422 means the tenant rejects the public CXS body shape.
make refresh-slugs-full
```

Discovery (the candidate-pull) only covers Greenhouse + Lever — the
SimplifyJobs feed doesn't carry Ashby or Workday URLs, and there's no
equivalent public list for them. Re-verification covers all four sources.

The SimplifyJobs feed is heavily new-grad / intern biased — it's only useful
as a wide net for *valid* slugs, not relevant ones. To add a target company
by hand, insert into the DB directly (or edit `companies.py` before your first
`init`). Failed fetches during ingest log a warning but don't break the run.

### Cross-source dedupe

Two dedupe layers run on every ingest:

- **Per-source hash** (`source + source_id`) — catches the same job appearing
  twice in the same source.
- **Cross-source hash** (normalized `(company, title)`) — collapses the same
  role surfaced via multiple sources (e.g. Stripe via Greenhouse + RemoteOK).

The cross-source hash is populated on every new insert; on existing rows it's
backfilled by `job-applier init`.

### Adding a brand-new source type

Create a file under `src/job_applier/sources/` that implements the
`SourceAdapter` protocol from `sources/base.py` (one method:
`fetch() -> Iterable[RawJob]`), then add an instance to `get_all_sources()` in
`sources/__init__.py`. If the source needs per-company config, add a seed list
to `companies.py` and a key to `_SEEDS` in `sources/refresh.py`.

## Why no Anthropic API calls?

You pay for Claude Code already. The `/match-pending` slash command (and the
forthcoming `/draft <job-id>`) lets the Claude Code session itself do the
reasoning, which keeps the project free of API keys and token costs.

## Make targets

| Command       | Description                                       |
| ------------- | ------------------------------------------------- |
| `make setup`  | `uv sync` + `npm install` for the web app         |
| `make ingest` | Pull jobs from configured sources                 |
| `make api`    | Run FastAPI on `:8000` with auto-reload           |
| `make web`    | Run SvelteKit dev server on `:5174`               |
| `make lint`   | `ruff check src/`                                 |
| `make clean`  | Remove build artifacts and caches                 |
