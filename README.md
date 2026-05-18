# job-applier

A personal job board. Pulls remote roles from open job sources, filters them
against a configurable search profile (role titles, seniority, required and
excluded tech), scores survivors against your resume via Claude Code, and
surfaces the results in a SvelteKit review UI so you can decide which ones are
worth tailoring an application for. Tailored resume + cover-letter drafts and
follow-up tracking are built in.

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

> **This is a single-user local tool.** The FastAPI server binds to `127.0.0.1`
> and has no authentication. CORS is locked to the local SvelteKit origin. Do
> not expose it on a public interface — anyone who can reach it can mutate your
> queue, your resume, and your search profile.

## Daily flow

1. **Run the API + UI** in two terminals:
   ```sh
   make api    # http://127.0.0.1:8000
   make web    # http://localhost:5174
   ```
2. **Upload your resume** at http://localhost:5174/resume — pick the PDF you
   actually send to employers. The API extracts plain text via `pypdf` and stores
   it as the active resume. Older uploads are kept but inactive.
3. **Configure your search profile** at http://localhost:5174/search — set role
   titles, seniority terms, required tech, and excluded tech. These drive the
   hard filter at ingest. Run `/suggest-roles` in Claude Code to have it propose
   a profile based on your resume; you accept or edit before it applies.
4. **Ingest** new postings:
   ```sh
   make ingest
   ```
5. **Score the queue** — open Claude Code in this repo and run:
   ```
   /match-pending
   ```
   It pulls the active resume from `/api/resume/current`, fetches unscored jobs
   (plus any whose score is stale because the resume changed), and writes scores
   back. Refresh the UI to see them.
6. **Review** in the UI — change a job's status to `interested`, `drafted`,
   `applied`, `screening`, `interviewing`, `rejected`, etc. Status changes use
   SvelteKit form actions, so they round-trip through the backend without
   client-side fetch code. Add jobs to the draft cart from any row; the cart
   persists across `/`, `/jobs/[id]`, and `/followups`.
7. **Draft tailored applications** for the jobs you want to apply to:
   ```
   /draft <job-id> [<job-id> ...]
   ```
   Writes a tailored resume + cover-letter markdown per job, renders both PDFs
   via weasyprint under `applications/<id>/`, and re-scores the tailored draft
   against the JD so you see a `baseline → tailored` delta. The job's status is
   moved to `drafted`.
8. **Track follow-ups** at http://localhost:5174/followups — applied jobs past
   their follow-up date surface here so nothing goes silent.

## Project layout

```
src/job_applier/
  api/         # FastAPI app + Pydantic schemas
  filters/     # Hard-rule filter, driven by SearchProfile
  models/      # SQLModel definitions + DB engine (jobs, scores, history, applications, profile)
  sources/     # Source adapters (Greenhouse, Lever, Ashby, Workday, RemoteOK, WWR, HN)
  ingest.py    # Pipeline: fetch → dedupe (per-source, cross-source, JD-SimHash) → filter → persist
  drafts.py    # Tailored resume / cover-letter markdown + weasyprint PDF rendering
  resume_io.py # PDF → text extraction + on-disk storage
  cli.py       # `job-applier` typer CLI
  config.py    # Settings (paths, ports, DB location)
web/           # SvelteKit app
  src/lib/api.ts                                       # typed client used by +page.server.ts
  src/lib/draftCart.svelte.ts                          # cross-route draft cart (Svelte rune-based store)
  src/routes/+page.{svelte,server.ts}                  # queue (persisted filters, source/status/ease chips)
  src/routes/jobs/[id]/+page.{svelte,server.ts}        # detail, status form actions, rubric popover, drafts
  src/routes/search/+page.{svelte,server.ts}           # search profile editor (review /suggest-roles draft)
  src/routes/followups/+page.{svelte,server.ts}        # applied jobs past their follow-up date
  src/routes/resume/+page.{svelte,server.ts}           # resume upload + view
.claude/commands/
  match-pending.md   # score the pending queue against the active resume
  draft.md           # /draft <job-id>...  tailored resume + cover letter
  score-draft.md     # /score-draft <job-id>...  re-score a tailored draft for the baseline → tailored delta
  suggest-roles.md   # propose a SearchProfile from the active resume
applications/        # generated tailored resumes / cover letters per job (gitignored)
data/jobs.db         # SQLite (gitignored)
data/resumes/        # uploaded PDFs (gitignored)
```

## Hard filter rules

Applied at ingest time. Jobs that fail the role criteria are dropped before
persistence (cheap to re-evaluate on every ingest). Jobs that fail the location
or remote checks are still written to the DB so they're auditable.

The role-specific criteria — seniority terms, required tech, excluded tech — live
on the `SearchProfile` row and are edited at http://localhost:5174/search. The
fixed rules, always applied, are:

- **Remote only** — drops `hybrid`, `on-site`, anything mentioning relocation.
- **US-locatable** — if the posting names a non-US country/region and has no US
  marker, drop. Specific "City, Region" locations without a US hint also drop.
- **State allow-list must include Missouri** — postings that say "we can only
  hire in X, Y, Z" and don't list MO drop. Phrased as "any US state" or
  "nationwide" overrides.
- **Not a sales / pre-sales / biz-dev title** — `Senior Solutions Engineer`,
  `Head of Partnerships`, etc. are dropped even when they pass seniority.

Then the per-profile rules:

- **Seniority** — title must contain one of `seniority_terms`.
- **Required tech** — posting body or tags must reference one of `required_tech`.
  Short tokens (≤2 chars, e.g. `js`, `ts`, `go`) only mark a posting as `manual`
  on their own; long-form matches pass cleanly.
- **Excluded tech** — an `excluded_tech` term in the title disqualifies; in the
  tags it disqualifies unless a competing framework from `required_tech` is also
  tagged. A mention only in the description with no positive required-tech
  signal there is surfaced as `manual` so you can decide.

Defaults shipped for fresh installs: senior+/staff/principal/lead seniority,
JS/TS family stacks, Angular excluded. Run `/suggest-roles` to have Claude Code
propose a profile from your resume; the recommendation is saved as a draft on
`SearchProfile.recommendations_draft` and applied only when you click through
the UI. The filter falls back to the built-in defaults whenever no profile row
exists or its required-tech list is empty.

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

### Dedupe

Three dedupe layers run during/after ingest:

- **Per-source hash** (`source + source_id`) — catches the same job appearing
  twice in the same source.
- **Cross-source hash** (normalized `(company, title)`) — collapses the same
  role surfaced via multiple sources (e.g. Stripe via Greenhouse + RemoteOK).
- **JD SimHash** — a 64-bit fingerprint of the description catches near-duplicate
  postings (reposts, aggregator copies with reworded titles) that slip past the
  first two. The match isn't dropped; it's soft-linked to its canonical posting
  via `JobPosting.duplicate_of` and hidden from the default listing.

Cross-source hashes are populated on every new insert. The SimHash pass is
incremental on each ingest and can be re-run / backfilled via `make dedupe-jd`.
On existing rows without a cross-source hash, it's backfilled by `job-applier
init`.

### Adding a brand-new source type

Create a file under `src/job_applier/sources/` that implements the
`SourceAdapter` protocol from `sources/base.py` (one method:
`fetch() -> Iterable[RawJob]`), then add an instance to `get_all_sources()` in
`sources/__init__.py`. If the source needs per-company config, add a seed list
to `companies.py` and a key to `_SEEDS` in `sources/refresh.py`.

## Slash commands

All LLM work runs inside Claude Code on this repo — no Anthropic API calls,
no API keys to manage. Each command reads from / writes to the local API.

| Command                       | What it does                                                                                                |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `/match-pending`              | Score every unscored job (and stale-scored jobs) against the active resume. Writes baseline scores.         |
| `/draft <id> [<id> ...]`      | Generate a tailored resume + cover letter per job (markdown + PDF), set status to `drafted`, score the tailored draft. |
| `/score-draft <id> [<id> ...]`| Re-score a tailored draft against the JD using the same rubric as `/match-pending`. Writes a `tailored`-kind score. |
| `/suggest-roles`              | Read the active resume and POST a recommended `SearchProfile` to `recommendations_draft` for review at `/search`. |

Scores are snapshotted to `MatchScoreHistory` whenever they're overwritten, so
the `baseline → tailored` delta and prior-resume scores remain visible.

## Make targets

| Command                  | Description                                                       |
| ------------------------ | ----------------------------------------------------------------- |
| `make setup`             | `uv sync` + `npm install` for the web app                         |
| `make api`               | Run FastAPI on `:8000` with auto-reload                           |
| `make web`               | Run SvelteKit dev server on `:5174`                               |
| `make ingest`            | Pull jobs from configured sources                                 |
| `make refresh-slugs`     | Discover new Greenhouse/Lever slugs from the SimplifyJobs feed    |
| `make refresh-slugs-full`| Discover + re-verify existing slugs (auto-disables dead boards)   |
| `make prune`             | Clear description/raw on old or archived postings (keeps hashes)  |
| `make dedupe-jd`         | Backfill JD SimHash fingerprints + soft-link near-duplicate JDs   |
| `make lint`              | `ruff check src/`                                                 |
| `make test`              | Run backend + frontend test suites                                |
| `make test-api`          | Backend tests (pytest)                                            |
| `make test-web`          | Frontend tests (vitest)                                           |
| `make clean`             | Remove build artifacts and caches                                 |
