# job-applier

A personal job board. Pulls remote roles from open job sources, filters them
against a configurable search profile (role titles, seniority, required and
excluded tech), scores survivors against your resume using an AI CLI you choose
(Claude Code, Gemini, Codex, or fully-local Ollama), and surfaces the results in
a SvelteKit review UI so you can decide which ones are
worth tailoring an application for. Tailored resume + cover-letter drafts and
follow-up tracking are built in.

No LinkedIn or Indeed scraping — those violate ToS and risk account bans.
Sources are open ATS endpoints (Greenhouse, Lever, Ashby, Workday, Workable,
SmartRecruiters, Jibe, Oracle) and aggregator feeds (RemoteOK, We Work Remotely,
Hacker News "Who is hiring", Y Combinator).

## Install (desktop app)

Prefer the packaged app — no Python, Node, or dev tools required. Download the
latest installer for your OS from the
[**Releases page**](https://github.com/hhagely/job-applier/releases/latest):

- **Windows** — `job-applier-Setup-<version>.exe` (per-user install, no admin).
  The app is **unsigned**, so on first run Windows SmartScreen shows
  *"Windows protected your PC / unknown publisher."* Click **More info → Run
  anyway**. (Expected for an unsigned indie app; there is no paid code-signing
  certificate.)
- **Linux** — `job-applier-<version>.AppImage` (`chmod +x` then run) or the
  `.deb` (`sudo apt install ./job-applier_<version>_amd64.deb`).
- **macOS** — not built yet (avoids the paid Apple notarization); run from
  source (**[Running from source](#running-from-source)**) if you're on a Mac.

On first launch the app walks you through a short **onboarding** flow: pick an
AI CLI, upload your resume, and pull the first batch of jobs. See
**[Getting started](#getting-started)** for the same setup with the reasoning
behind each step.

**AI-CLI prerequisite (optional).** Scoring and drafting run through an AI CLI
you install and log into — install **one** of
[Claude Code](https://docs.claude.com/en/docs/claude-code) or
[Gemini CLI](https://github.com/google-gemini/gemini-cli) (recommended), or
[Ollama](https://ollama.com) (fully local, best-effort). **Everything else
(ingest, filter, browse, track, PDF export) works with no AI CLI installed** —
the app just hides the scoring/drafting buttons until you add one at `/settings`.

**Updates.** The desktop app checks GitHub Releases on launch and, when a newer
version is out, shows an **Update** pill in the title bar. Opening it reveals what's
new; **Download & install** fetches the new version (with a progress bar), then
**Restart to install** swaps the app in place, keeping your data (electron-updater).
You can also check any time from **Settings → About & updates** or the ⌘/Ctrl-K
command palette. The installer is unsigned, so the same one-time SmartScreen appears.
On Linux both packages self-update: the AppImage swaps itself, and the `.deb` installs
through `dpkg` behind a password prompt (if neither `dpkg` nor `apt` is available, an
error is shown instead).

## Getting started

The first launch opens a three-step **onboarding wizard** — pick an AI CLI, upload
your resume, pull your first batch of jobs. You can skip any step and finish later;
the app remembers and won't trap you back in the wizard. What follows is the same
setup with the reasoning the wizard has no room for.

### 1. Pick an AI CLI — optional, but do it first

Scoring and drafting shell out to an AI CLI you install and log into yourself; the
app never touches an API key. Install **one** and select it under **Settings**:

- **Claude Code** (`claude`) or **Gemini CLI** (`gemini`) — recommended.
- **Codex CLI** (`codex`) or **Ollama** (`ollama`, fully local, no subscription) — best-effort.

Settings lists whichever CLIs are on your `PATH` and can run a live test against the
one you pick, so you find out it works here rather than halfway through a scoring run.

**Everything except scoring and drafting works with no provider at all** — ingest,
filtering, browsing, status tracking, and follow-ups are provider-free, and the app
simply hides the AI buttons. Do this step first anyway: step 3 has a "propose a
profile from my resume" shortcut that needs a provider.

### 2. Upload your resume

**Resume** in the sidebar. Use the PDF you actually send to employers, and make sure
it's a **text-based PDF rather than a scan** — the text is extracted with `pypdf`, and
an image-only PDF extracts to nothing, which quietly ruins every score that follows.

This one document does double duty: it's what every job is scored against, and the
base that tailored drafts are rewritten from. It never leaves your machine.

Uploading a **new** resume later invalidates every existing score at once. Staleness
is an id comparison, not a content diff, so a typo fix reads exactly like a rewrite —
which is why the page asks you to judge it, right then:

- **Keep existing scores** — re-stamps them onto the new resume, no AI calls. Right
  for a typo or a reworded bullet.
- **Re-score** — spends AI calls to redo them. Right when you rewrote a section or
  changed your titles or tech.

### 3. Fill out your search profile

**Search profile** in the sidebar. These fields drive the *hard filter*, which runs
during ingest — a posting that fails is dropped before it ever reaches your queue.
That makes this the highest-leverage screen in the app, and the easiest one to
over-tighten.

| Field | Effect |
| --- | --- |
| **Seniority terms** | The job title must contain one of these. The strictest rule here — `senior` alone excludes every `Staff` and `Principal` posting. |
| **Required tech** | The posting body or tags must reference one. Tokens of ≤2 characters (`js`, `go`, `ml`) are too ambiguous to pass on their own, so on their own they route a posting to **Manual review** instead. |
| **Excluded tech** | In the title, disqualifies outright. In the tags, disqualifies unless a competing required-tech framework is also tagged. Mentioned only in the description with no positive signal, it goes to **Manual review** so you decide. |
| **State of residence** | Optional. Drops postings whose "we can only hire in X, Y, Z" list leaves your state out. **Unset skips the rule entirely** — no state is assumed. Used only for this filter, stored locally, never sent anywhere. |
| **Role titles** | The roles you're targeting. Recorded on the profile and filled in by *Suggest roles*; descriptive rather than functional — seniority and tech do the actual filtering. |

Click **Suggest roles from resume** (needs a provider from step 1) to have the model
read your resume and propose a whole profile. It's saved as a *draft* and changes
nothing until you review and accept it.

**Start broader than feels right.** The filter is unforgiving and silent: a profile
that's too narrow doesn't warn you, it just hands you an empty queue, and the
postings it dropped are gone rather than waiting somewhere. Widen first, then tighten
once you can see what's coming through. The **Manual review** tab on the queue is
where the ambiguous calls land — worth a look, since a thin queue often means good
roles are piling up there.

Two more tools on the same page, both aimed at *which employers* get searched:

- **Company blacklist** — employers you never want to see. Checked before every other
  rule, so their postings are dropped without a row being written. Matching normalizes
  the name, so `Meta`, `Meta Inc`, and `Meta, Inc.` are one entry however a source
  spells it. Edits only affect future ingests, not rows you already have.
- **Check a company** — name an employer to find out whether your scrapes already
  cover them, and add their job board if not. About 1,300 company boards ship seeded,
  so the usual answer is "already covered". See
  [Managing the company slug list](#managing-the-company-slug-list) for what can and
  can't be added by hand.

### 4. Run your first scrape

**Dashboard → Run scrape** (or ⌘/Ctrl-K → *Run scrape now*). It runs as a background
task with a live progress bar, and you can navigate away while it works.

Expect the first run to take a while: it walks roughly 1,300 seeded company boards
plus the config-free aggregators. Only postings that survive the hard filter are
saved, so a few thousand fetched postings can land as a queue of a few dozen. Failed
sources log a warning and don't sink the run.

### 5. Score the queue

**Dashboard → Score pending (N)**. The backend pulls your active resume and scores
each unscored job against its description via your AI CLI — batched into one call per
group, with a per-job retry so a single bad response can't poison a batch. Another
background task; scores land in the queue as they arrive.

**Jobs scoring below 60 are auto-archived** to keep the queue readable. Only
*untriaged* jobs are — anything you've already marked `interested`, `applied`, and so
on keeps its status. Archived jobs aren't deleted: flip **Show archived** on the queue
to audit what was dropped and why.

Scores read as bands, not precise numbers: **80+** strong, **65–79** worth a look,
**below 65** weak. Treat the rubric breakdown in the detail pane as the real output —
it tells you *why*, which is what you need to decide whether to apply.

### 6. Review the queue

**Queue** (`/`) is a master–detail list: rows on the left, match breakdown on the
right. Triage by setting a status — `interested`, `drafted`, `applied`, `screening`,
`interviewing`, `rejected`, `archived`. Filter chips narrow by status, source, ease of
applying, and minimum score, and your filter selection persists between sessions.

Each posting also carries a **Mark used for unemployment** toggle, if you need a dated
log of applications for a claim.

### 7. Draft tailored applications

Build up a **draft list**, then run it in one go. Add the selected job from the detail
pane (**+ Add to draft list**), or tick several rows and use **Add to draft list** on
the selection bar; the list follows you across the queue, job detail, and follow-up
pages. Hit **Draft list (N)** on the queue to run it, or draft a single job from its
detail page.

Each run writes a tailored resume and cover letter as markdown, renders both to PDF,
moves the job to `drafted`, and re-scores the tailored version so you get a
`baseline → tailored` delta.

The markdown is the master copy and it's editable: fix a line, then **Re-render PDFs
from markdown**. No need to regenerate unless you want the model to rebuild it from
the job description.

Be selective. Drafting is the most expensive thing the app does, and a tailored
application you didn't read is worse than a good generic one.

### 8. Track follow-ups

Applying sets a follow-up date automatically. **Follow-ups** collects applications
past that date with no outcome recorded, so nothing goes quiet without you noticing.

### The loop after setup

Scrape → score → review → draft the few worth it → follow up. Scraping daily is
plenty; sources don't turn over faster than that.

### Finding things

⌘/Ctrl-K opens the command palette, which searches your ingested postings by **job
title or company** as well as running commands. It reaches archived and manual-review
postings too, so it's the fastest way back to a job you remember seeing.

| Shortcut | Action |
| --- | --- |
| `⌘/Ctrl-K` or `/` | Command palette + job/company search |
| `⌘/Ctrl-1…6` | Jump to Dashboard / Queue / Follow-ups / Resume / Search profile / Settings |
| `J` / `K` | Next / previous job in the queue |
| `⌘/Ctrl-J` | Toggle light / dark theme |
| `?` | Show the in-app shortcut sheet |
| `Esc` | Close any overlay |

## Architecture

```
┌──────────────┐   ingest   ┌──────────┐   filter   ┌──────────┐   HTTP   ┌──────────┐
│  Source(s)   │ ─────────► │ SQLite   │ ─────────► │ FastAPI  │ ◄──────► │ SvelteKit│
│ Greenhouse,… │            │ jobs.db  │            │ :8000    │          │ :5174    │
└──────────────┘            └──────────┘            └────┬─────┘          └──────────┘
                                                         │ spawns a sandboxed
                                                         ▼ subprocess per job
                                                ┌───────────────────┐
                                                │ AI CLI you pick:  │
                                                │ claude / gemini / │
                                                │ codex / ollama    │
                                                └───────────────────┘
```

The LLM runs server-side, but only by shelling out to an AI CLI you install and
pick in the app — never through an SDK or a raw API key. Scoring, cover-letter
drafting, and role suggestions spawn the selected provider (Claude Code, Gemini
CLI, Codex CLI, or local Ollama) as a **sandboxed** subprocess: no tools, a
scrubbed environment, a throwaway working directory, and a timeout (job
descriptions are untrusted scraped text, so model output is treated as data).
Each CLI authenticates via its own login, so there are no API keys to manage —
and the app runs fully, minus the AI features, with no provider installed.

## Running from source

Only needed if you're on macOS (no packaged build yet) or working on the app
itself. The **[Getting started](#getting-started)** walkthrough above applies
either way — same UI, same flow.

```sh
make setup                # uv sync + npm install
uv run job-applier init   # create the SQLite DB
```

Then run the two halves in two terminals:

```sh
make api    # FastAPI  → http://127.0.0.1:8000
make web    # SvelteKit → http://localhost:5174
```

Open http://localhost:5174 and follow the onboarding wizard. `make app-dev` is a
one-command alternative that boots both on free ports and opens a browser.

The day-to-day flow all lives in the UI, but some maintenance has no button:

```sh
make ingest             # same as Dashboard → Run scrape
make diagnose-filter    # dry-run every source and report what the filter drops,
                        #   persisting nothing — the tool to reach for when your
                        #   queue looks thin and you can't tell whether it's
                        #   sourcing or over-tight filter settings
make prune              # lighten old/archived postings (keeps dedupe hashes)
make dedupe-jd          # backfill JD SimHashes, soft-link near-duplicates
```

> **This is a single-user local tool.** The FastAPI server binds to `127.0.0.1`
> and has no authentication. CORS is locked to the local SvelteKit origin. Do
> not expose it on a public interface — anyone who can reach it can mutate your
> queue, your resume, and your search profile.

## Project layout

```
src/job_applier/
  api/         # FastAPI app + Pydantic schemas (includes api/ai.py — the AI router)
  ai/          # Provider-agnostic AI layer: sandboxed CLI runner (providers.py),
               #   scoring / drafting / suggest, char-ban enforcement, background
               #   task runner, and the canonical prompt templates (ai/prompts/)
  filters/     # Hard-rule filter, driven by SearchProfile
  models/      # SQLModel definitions + DB engine (jobs, scores, history, applications, profile)
  sources/     # Source adapters (Greenhouse, Lever, Ashby, Workday, Workable, SmartRecruiters, Jibe, Oracle, RemoteOK, WWR, HN, YC)
  ingest.py    # Pipeline: fetch → dedupe (per-source, cross-source, JD-SimHash) → filter → persist
  drafts.py    # Tailored resume / cover-letter markdown + PDF persistence (rendering in pdf.py)
  resume_io.py # PDF → text extraction + on-disk storage
  cli.py       # `job-applier` typer CLI
  config.py    # Settings (paths, ports, DB location)
web/           # SvelteKit app
  src/lib/api.ts                                       # typed client used by +page.server.ts
  src/lib/draftCart.svelte.ts                          # cross-route draft cart (Svelte rune-based store)
  src/routes/+page.{svelte,server.ts}                  # queue (persisted filters, source/status/ease chips)
  src/routes/jobs/[id]/+page.{svelte,server.ts}        # detail, status form actions, rubric popover, drafts
  src/routes/search/+page.{svelte,server.ts}           # search profile editor (review /suggest-roles draft) + company black/whitelist
  src/routes/followups/+page.{svelte,server.ts}        # applied jobs past their follow-up date
  src/routes/resume/+page.{svelte,server.ts}           # resume upload + view
.claude/commands/    # legacy Claude-Code slash commands (mirror src/job_applier/ai/prompts/)
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

**Company blacklist (checked first).** Before any rule runs, a job whose employer
is on your company blacklist is dropped outright — no row is written, even the
first time that company is seen. The list is edited at
http://localhost:5174/search alongside the profile. Matching normalizes the
company name (casing, punctuation, and one trailing legal suffix), so `Meta`,
`Meta Inc`, and `Meta, Inc.` all match however a source spells it. Editing the
list only affects future ingests, not rows already saved.

The role-specific criteria — seniority terms, required tech, excluded tech — live
on the `SearchProfile` row and are edited at http://localhost:5174/search. The
fixed rules, always applied, are:

- **Remote only** — drops `hybrid`, `on-site`, anything mentioning relocation.
- **US-locatable** — if the posting names a non-US country/region and has no US
  marker, drop. Specific "City, Region" locations without a US hint also drop.
- **State allow-list must include your home state** — postings that say "we can
  only hire in X, Y, Z" and don't list your state drop. Phrased as "any US state"
  or "nationwide" overrides. Set your state of residence at
  http://localhost:5174/search; **when it's left unset this rule is skipped
  entirely** (no state is assumed). Your state is used only for this ingest filter,
  stored locally, and never sent anywhere.
- **Not a sales / pre-sales / biz-dev title** — `Senior Solutions Engineer`,
  `Head of Partnerships`, etc. are dropped even when they pass seniority.
- **Not crypto / blockchain / web3** — matched against the whole posting, not just
  the title.

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
JS/TS family stacks, Angular excluded. **Suggest roles from resume** on `/search`
has your selected AI CLI propose a profile from your resume; the recommendation is
saved as a draft on `SearchProfile.recommendations_draft` and applied only when you
accept it in the UI. The filter falls back to the built-in defaults whenever no
profile row exists or its required-tech list is empty.

## Sources

| Source           | Config                          | Notes                                                                 |
| ---------------- | ------------------------------- | --------------------------------------------------------------------- |
| Greenhouse       | DB slug list (`SourceSlug`)     | `boards-api.greenhouse.io/v1/boards/{slug}/jobs`                      |
| Lever            | DB slug list (`SourceSlug`)     | `api.lever.co/v0/postings/{slug}`                                     |
| Ashby            | DB slug list (`SourceSlug`)     | `api.ashbyhq.com/posting-api/job-board/{slug}`. The API accepts either casing, but the slug is used as the employer name in your queue, so discovery keeps the branded spelling the feed carries (`Notion`, not `notion`) and dedupes case-insensitively. |
| Workday          | DB slug list, packed format     | Slug is `{tenant}\|{region}\|{site}` — e.g. `salesforce\|wd12\|External_Career_Site`. List call returns only titles; descriptions need a per-posting detail fetch, so the adapter pre-filters titles before going deep. |
| Workable         | DB slug list (`SourceSlug`)     | `apply.workable.com/api/v3/accounts/{slug}/jobs` (list) + v1 detail for the full description. |
| SmartRecruiters  | DB slug list (`SourceSlug`)     | `api.smartrecruiters.com/v1/companies/{slug}/postings`. Slugs are case-sensitive (`Visa` ≠ `visa`). |
| Jibe (iCIMS)     | DB slug list (`SourceSlug`)     | `{tenant}.jibeapply.com/api/jobs`. Slug is the jibeapply.com subdomain (e.g. `githubinc`). |
| Oracle (Recruiting Cloud) | DB slug list, packed format | Slug is `{apiHost}\|{siteNumber}\|{publicJobBaseUrl}[\|{company}]`. Served by the candidate-experience `recruitingCEJobRequisitions` JSON API on the underlying Fusion host (the vanity careers domain 302s API calls away); job links use the public base. |
| RemoteOK         | none                            | Single-endpoint aggregator (`remoteok.com/api`).                      |
| We Work Remotely | none                            | Per-category RSS feeds; engineering categories only.                  |
| Hacker News      | none                            | Most recent monthly "Who is hiring" thread, parsed via Algolia HN API. Top-level comments are individual postings. |
| Y Combinator     | none                            | HN `jobstories` feed + JSON-LD scraped from `ycombinator.com` job pages. |

### Managing the company slug list

Per-company slugs (Greenhouse, Lever, Ashby, Workday, Workable, SmartRecruiters,
Jibe, Oracle) live in the database (`SourceSlug` table), not in code. Initial
setup seeds the table from `src/job_applier/sources/companies.py` on first
`job-applier init` — and the seed is per-source, so adding a new source type
later picks up its seed on the next `init` without disturbing the populated
tables.

```sh
# Pull new Greenhouse/Lever/Workable/SmartRecruiters candidates from the
# SimplifyJobs feed and verify them.
make refresh-slugs

# Same, but also re-verify every existing slug and auto-disable dead boards.
# A Workday tenant returning HTTP 422 is treated as a permanent rejection
# and disabled, since 422 means the tenant rejects the public CXS body shape.
make refresh-slugs-full
```

Discovery (the candidate-pull) covers the five sources the SimplifyJobs feed
carries URLs for: Greenhouse, Lever, Ashby, Workable, and SmartRecruiters.
There's no equivalent public list for the others. Re-verification is broader —
it covers those five plus Workday, auto-disabling dead boards; Jibe and Oracle
are seed-only (neither discovered nor re-verified).

Sources differ in what counts as proof a board is real, so `board_exists()` in
[refresh.py](src/job_applier/sources/refresh.py) holds the rule for both
discovery and the manual add. Greenhouse, Lever, and Ashby 404 a slug they don't
know, so any 200 is proof — a board with no openings today is a real employer
who just isn't hiring. SmartRecruiters answers 200 with an empty list for *any*
string, and Workable keeps abandoned accounts alive forever, so on those two a
board only counts when it currently has at least one open posting.

The SimplifyJobs feed is heavily new-grad / intern biased — it's only useful
as a wide net for *valid* slugs, not relevant ones. Failed fetches during
ingest log a warning but don't break the run.

**Checking or adding one company by hand.** Use **Check a company** on
http://localhost:5174/search: type the employer's name and the app derives slug
candidates and probes every source it can check from a bare slug (applying the
same `board_exists` rule above), or paste the URL of their job board for an
exact match. A URL is the only way to add a Workday tenant, whose slug packs
`tenant|region|site`; Oracle can't be added this way at all, since its slug
carries an internal Fusion API host and a numeric site id no public URL exposes.
Employers who run a custom careers page rather than a supported ATS are out of
reach entirely — the check will say so rather than guess.

If the company is already being searched — including the usual case where the
seed or the SimplifyJobs feed already covered it — you're told so and nothing is
added twice; the match is on the same normalized name key the blacklist uses, so
`Acme Corp` finds the stored slug `acme-corp`. A pasted URL is checked the same
way, including against the same employer already watched on a different ATS. Hand-added boards are marked in
the DB (`SourceSlug.added_by_user`) so the page can list back just yours out of
the thousand-plus discovered ones, and removing one there deletes only that row.

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

## AI flows

All LLM work runs server-side by shelling out to the AI CLI you selected at
`/settings` — no Anthropic/OpenAI SDK, no API keys to manage (each CLI uses its
own login), and every call is sandboxed as described under Architecture. The
flows are triggered from the UI; the equivalent **legacy** Claude-Code slash
command is listed for anyone who prefers to drive it from a Claude Code session
on this repo.

| Flow (UI trigger)                                     | Legacy command             | What it does                                                                                                    |
| ----------------------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Score pending** (`/dashboard`)                      | `/match-pending`           | Score every unscored job (and stale-scored jobs) against the active resume. Writes baseline scores.            |
| **Draft list** (queue cart) or draft one (`/jobs/[id]`)| `/draft <id> ...`          | Generate a tailored resume + cover letter per job (markdown + PDF), set status to `drafted`, re-score the draft.|
| _(runs automatically after each draft)_               | `/score-draft <id> ...`    | Re-score a tailored draft against the JD using the same rubric as scoring. Writes a `tailored`-kind score.      |
| **Suggest roles from resume** (`/search`)             | `/suggest-roles`           | Read the active resume and propose a `SearchProfile` to `recommendations_draft` for review at `/search`.        |

The prompt templates that define the scoring rubric and ATS-format rules live in
`src/job_applier/ai/prompts/` (canonical); the `.claude/commands/` files mirror
them. Scores are snapshotted to `MatchScoreHistory` whenever they're overwritten,
so the `baseline → tailored` delta and prior-resume scores remain visible.

## Make targets

| Command                  | Description                                                       |
| ------------------------ | ----------------------------------------------------------------- |
| `make setup`             | `uv sync` + `npm install` for the web app                         |
| `make api`               | Run FastAPI on `:8000` with auto-reload                           |
| `make web`               | Run SvelteKit dev server on `:5174`                               |
| `make app-dev`           | Boot API + built web server on free ports and open a browser      |
| `make ingest`            | Pull jobs from configured sources                                 |
| `make diagnose-filter`   | Dry-run every source and report what the hard filter drops        |
| `make refresh-slugs`     | Discover new Greenhouse/Lever/Ashby/Workable/SmartRecruiters slugs from SimplifyJobs |
| `make refresh-slugs-full`| Discover + re-verify existing slugs (auto-disables dead boards)   |
| `make prune`             | Clear description/raw on old or archived postings (keeps hashes)  |
| `make dedupe-jd`         | Backfill JD SimHash fingerprints + soft-link near-duplicate JDs   |
| `make lint`              | `ruff check src/`                                                 |
| `make test`              | Run backend + frontend test suites                                |
| `make test-api`          | Backend tests (pytest)                                            |
| `make test-web`          | Frontend tests (vitest)                                           |
| `make clean`             | Remove build artifacts and caches                                 |
