# job-applier

A personal job board. Pulls remote roles from open job sources, filters them
against hard rules (Senior+ JS/TS, no Angular), scores survivors against your
resume via Claude Code, and surfaces the results in a SvelteKit review UI so you
can decide which ones are worth tailoring an application for.

No LinkedIn or Indeed scraping — those violate ToS and risk account bans.
Sources are open ATS endpoints (Greenhouse, Lever, Ashby — coming) and aggregator
APIs (Remotive — wired up; Adzuna, USAJobs — coming).

## Architecture

```
┌──────────────┐   ingest   ┌──────────┐   filter   ┌──────────┐
│  Source(s)   │ ─────────► │ SQLite   │ ─────────► │ FastAPI  │
│ Remotive,…   │            │ jobs.db  │            │ :8000    │
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

Drop your real resume into `resume/master.md` (replace the template).

## Daily flow

1. **Ingest** new postings:
   ```sh
   make ingest
   ```
2. **Run the API + UI** in two terminals:
   ```sh
   make api    # http://127.0.0.1:8000
   make web    # http://localhost:5174
   ```
3. **Score the queue** — open Claude Code in this repo and run:
   ```
   /match-pending
   ```
   It reads `resume/master.md`, fetches unscored jobs from the API, and writes
   scores back. Refresh the UI to see them.
4. **Review** in the UI — change a job's status to `interested`, `applied`,
   `rejected`, etc. Status changes use SvelteKit form actions, so they round-trip
   through the backend without client-side fetch code.

## Project layout

```
src/job_applier/
  api/         # FastAPI app + Pydantic schemas
  filters/     # Hard-rule filter (remote, Senior+, JS/TS, no Angular)
  models/      # SQLModel definitions + DB engine
  sources/     # Source adapters (Remotive today; more coming)
  ingest.py    # Pipeline: fetch → dedupe → filter → persist
  cli.py       # `job-applier` typer CLI
  config.py    # Settings (paths, ports, DB location)
web/           # SvelteKit app
  src/lib/api.ts                                 # typed client used by +page.server.ts
  src/routes/+page.{svelte,server.ts}            # queue
  src/routes/jobs/[id]/+page.{svelte,server.ts}  # detail + status form actions
.claude/commands/
  match-pending.md   # slash command Claude Code uses to score the queue
resume/master.md     # your resume (markdown, source of truth)
applications/        # generated tailored resumes / cover letters per job (gitignored)
data/jobs.db         # SQLite (gitignored)
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

## Adding a source

Create a new file under `src/job_applier/sources/` that implements the
`SourceAdapter` protocol from `sources/base.py` (one method: `fetch() -> Iterable[RawJob]`),
then add an instance to `ALL_SOURCES` in `sources/__init__.py`. The ingest
pipeline picks it up automatically.

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
