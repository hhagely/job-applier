// ===========================================================================
// SHARED REVIEW ENGINE — emitted by generate-project-skills.
//
// The engine LOGIC below is 100% stack-agnostic and must never be rewritten
// per-project. The only project-specific part of the emitted file is the
// `PROJECT_CONFIGS` object, which the generator fills in at the marked slot
// (see "GENERATED CONFIGS" below). Each entry is one launcher's config: the
// agent lineup + checklists, source globs, test command, default branch,
// preamble, category ordering, verdict labels/threshold and mode.
//
// WHY THE CONFIG LIVES HERE AND NOT IN `args`: a workflow script's only two
// inputs are its own text and `args`. The script text has no practical size
// limit, but a large `args` payload gets stringified by the harness and the
// string arrives TRUNCATED — a real ~17.9KB config died in the engine's own
// JSON.parse before a single agent ran, while a 50-byte payload arrived intact
// as an object. Workflow scripts also have no file or network access (no
// require / process / Bun / fetch / import()), so a sibling config file is not
// an option. Therefore: static config travels in the script, and `args` carries
// ONLY small runtime scope. Keep it that way.
//
// One file serves all three launchers, keyed by `configKey` (NOT by mode —
// codebase-audit and test-quality-audit are both mode:'audit'):
//   - code-review        → mode:'diff',  scope = the branch diff (auto-fix, if
//                           any, happens in the launcher's MAIN LOOP, not here)
//   - codebase-audit     → mode:'audit', scope = all source files
//   - test-quality-audit → mode:'audit', scope = all tests + source
//
// If you change this engine, re-run the prototypes before shipping.
// ===========================================================================

export const meta = {
  name: 'review-engine',
  description: 'Config-driven full-codebase audit / branch-diff review: deterministic agent fan-out → schema-enforced findings → semantic merge → JS verdict. No auto-fix (returns a report).',
  phases: [
    { title: 'Review' },
    { title: 'Merge' },
  ],
}

// ---------------------------------------------------------------------------
// GENERATED CONFIGS — the generator REPLACES this whole object, one entry per
// emitted launcher, keyed by skill name. Everything project-specific lives
// here; the logic below stays generic. Leave the markers in place so
// update-project-skills can find the slot again.
//
//   const PROJECT_CONFIGS = {
//     'code-review':        { mode: 'diff',  projectName: '…', agents: […] },
//     'codebase-audit':     { mode: 'audit', projectName: '…', agents: […] },
//     'test-quality-audit': { mode: 'audit', projectName: '…', agents: […] },
//   }
// ---------------------------------------------------------------------------

/* >>> BEGIN GENERATED CONFIGS >>> */
const PROJECT_CONFIGS = {
  "code-review": {
    "mode": "diff",
    "projectName": "job-applier",
    "defaultBranch": "main",
    "testCommand": "make test",
    "categoryOrder": [
      "architecture",
      "security",
      "migrations",
      "correctness",
      "error-handling",
      "tests",
      "sveltekit",
      "dry",
      "docs",
      "best-practices"
    ],
    "verdictLabels": {
      "fail": "BLOCK",
      "warn": "NEEDS WORK",
      "pass": "PASS"
    },
    "warningThreshold": 3,
    "mergeExtraInstructions": "5. PRE-EXISTING vs NEW: this is a branch-diff review. Downgrade findings on pre-existing code that the branch only touched incidentally by one severity level (critical->warning, warning->suggestion), UNLESS the finding is a security or data-corruption risk (keep those at full severity). Findings on code the branch actually added/changed stay at full severity.",
    "preamble": "You are reviewing the changes on a branch vs main in job-applier: a Python 3.12 FastAPI + SQLModel (SQLite) backend with a typer CLI under src/job_applier/, a SvelteKit 2 + Svelte 5 (runes) + TypeScript frontend under web/src/, and an Electron desktop shell under desktop/. Scope is the branch diff, not the whole codebase. Work through every item in the checklist below, reading the relevant code for each. When an item says to grep or search, run the search and base the result on its output. Aim for repeatable results: the same diff should yield the same findings. Read CLAUDE.md (and web/CLAUDE.md for frontend files) and honor its conventions. Report each issue via the structured schema (an item that passes produces no finding); severity must be one of critical, warning, suggestion.",
    "scopeInstructions": "REVIEW SCOPE = the branch diff vs main. Get the changed files with `git diff main...HEAD --name-only` (exclude web/node_modules/, web/.svelte-kit/, web/build/, desktop/dist/, desktop/node_modules/, data/, applications/, *.lock, package-lock.json) and the full diff with `git diff main...HEAD` (scope to a file with `-- <path>`). Focus findings ONLY on what the diff changed/added; caller-impact and dangling-reference checks may grep the whole repo. Do NOT audit unrelated files.",
    "agents": [
      {
        "key": "test-quality",
        "category": "tests",
        "label": "Test Quality & Coverage",
        "prompt": "Conventions: backend pytest under tests/ (test_<module>.py; the `make_raw` RawJob factory in tests/conftest.py; in-memory SQLite via create_engine(\"sqlite://\", poolclass=StaticPool) plus app.dependency_overrides[get_session] for API tests; @pytest.mark.parametrize for table cases). Frontend vitest + @testing-library/svelte, colocated: web/src/lib/*.test.ts, web/src/lib/__tests__/, and page.svelte.test.ts beside a route's +page.svelte. Run with `make test`.\n\nCALIBRATE FOR GOOD TESTS, NOT COVERAGE: a missing test is a finding ONLY when real logic could ship a bug without it - never because 'everything should have a test'. A low-value/padding test (asserts nothing real) is itself a finding, to trim.\n\n1. For every changed source file under src/job_applier/ and web/src/ (exclude generated files and *.d.ts): (a) find its tests - backend: tests/test_<module>.py or any tests/ file importing the module/function (grep the name); frontend: the colocated *.test.ts / page.svelte.test.ts - and report whether they exist; (b) if they exist, verify the new/changed BEHAVIORS (branches, error paths, state transitions, HTTP status codes) are tested - NOT that every symbol has a test; (c) if none exist, flag ONLY logic-bearing code where a regression would ship a bug: ingest/dedupe/filters/matching, scoring persistence, migrations, form actions = critical; other real branching = warning. Do NOT flag thin routers that only delegate to services, Pydantic/SQLModel field declarations, pure constants, or presentational .svelte markup.\n2. Read every changed test file: (a) assertions test OUTCOMES (rows written, response JSON + status code, rendered text), not implementation; (b) flag tests that only assert `is not None` / `toBeTruthy()` / 'does not raise', plus tautological or over-mocked tests that assert nothing real - recommend trimming or rewriting them; (c) DATA SAFETY: a test that enters `TestClient(app)` (which runs the app lifespan and so create_db_and_tables()) or calls `engine()` / `Session(engine())` without the DB path isolated (an autouse isolation fixture in tests/conftest.py, or monkeypatching settings.db_path / models.db._engine to a temp DB) = critical: it migrates and writes the developer's real data/jobs.db; (d) a test stubbing a function the code now calls with a new keyword argument (e.g. a `lambda kind, total, fn, ref=None:` stub for tasks.start_task) must accept it.\n3. For every new if/elif/early-return/except branch in the diff, check a test exercises it. Untested error or state-transition branches = warning.\n4. For every new or changed API endpoint or form action, check a test covers its failure status (422/404/409/503) as well as the success path."
      },
      {
        "key": "dry",
        "category": "dry",
        "label": "DRY Code",
        "prompt": "1. Read every changed source file (.py under src/job_applier/, .ts / .svelte under web/src/, .js under desktop/; not tests). For each: (a) a code block (3+ lines) repeated more than once in the same file = warning; (b) a block nearly identical to code in another source file = warning.\n2. For each pattern found, grep the full repo (src/job_applier/, web/src/, desktop/) for ALL occurrences and report count + locations. Flag only if it appears 3+ times OR extraction meaningfully reduces maintenance burden.\n3. Repeated literals used as setting keys, status names, task kinds, route paths, or query params across files - flag values appearing 3+ times. Setting keys and bounds belong in src/job_applier/contracts.py; status/facet values must come from the Python enums or web/src/lib/api.ts constants (APPLICATION_STATUSES).\n4. Shared frontend abstractions already exist (taskRunner.svelte.ts, jobActions.server.ts, StatusTrackingCard, JobDescription): flag new code re-implementing one of them.\n5. Do NOT flag: test verbosity, SQLModel/Pydantic field repetition, or two-site patterns in clearly different contexts."
      },
      {
        "key": "architecture",
        "category": "architecture",
        "label": "Architecture",
        "prompt": "Layering (CLAUDE.md): routers in src/job_applier/api/ map HTTP <-> DTOs (api/schemas.py, api/serializers.py); src/job_applier/services.py is the shared persistence layer used by both the HTTP routes and the AI orchestrator and must NOT import job_applier.api; contracts.py holds shared keys/defaults/bounds; models/db.py is the schema; source adapters implement the SourceAdapter protocol (sources/base.py) and are registered in sources/__init__.py; the AI layer lives in src/job_applier/ai/. Frontend: web/src/lib/api.ts is the typed client; cross-route state lives in *.svelte.ts rune stores; the desktop shell in web/src/lib/shell/.\n\n1. For every changed module: (a) God object - LOC, function count, distinct responsibilities; flag modules past ~400 lines that mix concerns (HTTP + persistence + business rules) as warning, past ~700 or 3+ concerns as critical. (b) Duplicated source of truth: state held in two places (e.g. a preference stored both in AppSetting and on SearchProfile, a status mirrored in two tables) = critical. (c) Dependency direction: grep for `from job_applier.api` / `import job_applier.api` inside services.py, ingest.py, ai/, sources/, models/ = critical (the application layer must not depend on the web edge).\n2. NEVER HOLD A DB TRANSACTION ACROSS NETWORK OR SUBPROCESS I/O (CLAUDE.md): a `with Session(...)` / open session that spans `source.fetch()`, httpx calls, `subprocess.run`, or a provider call blocks every other SQLite writer for its whole duration. Flag as critical. Writes must be batched into short transactions (see run_ingest / INGEST_BATCH_SIZE).\n3. Data-shape choice: table row -> SQLModel in models/db.py; request/response -> Pydantic in api/schemas.py; cross-module constants/keys/bounds -> contracts.py; transient -> dataclass. Flag misuse (e.g. returning ORM rows from an endpoint, DTOs in services.py) as warning.\n4. New source adapters: implement SourceAdapter, registered in sources/__init__.py, per-company ones read slugs from SourceSlug at runtime (not hard-coded). Flag drift as warning.\n5. Long work runs as a background task (ai/tasks.py start_task, polled/streamed by the UI), never inside a request handler. Flag a new synchronous endpoint doing multi-second work as warning."
      },
      {
        "key": "best-practices",
        "category": "best-practices",
        "label": "Python / TypeScript Best Practices",
        "prompt": "1. Python - for every changed .py file under src/job_applier/: (a) public functions have typed params and return types; modules start with `from __future__ import annotations` - missing = warning. (b) No bare `except:`; no `except Exception` that silently swallows (Error Handling covers semantics; flag the syntax here). (c) No `print(` in src/job_applier/ - the CLI (cli.py) uses typer.echo, everything else logging = warning. (d) f-strings over %-formatting / .format() in new code = suggestion. (e) Would `make lint` (ruff check src/) flag it: unused imports/variables, import order.\n2. TypeScript / Svelte - for every changed .ts / .svelte file under web/src/: (a) exported functions have explicit types; no `any` leaking into the typed api surface = warning. (b) `import type` for type-only imports. (c) No `console.log(` in committed code - grep web/src/ = warning. (d) Must pass `cd web && npm run check` (svelte-check).\n3. Repo hard rules (carry verbatim - these catch more than 'follow conventions'): (a) NO AI SDKs OR API KEYS: grep for `import anthropic`, `from anthropic`, `import openai`, `from openai`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` = critical. AI runs server-side ONLY by shelling out to a user-installed CLI via src/job_applier/ai/providers.py; that is allowed. (b) NO LINKEDIN / INDEED: grep src/job_applier/sources/ for `linkedin.com` / `indeed.com` = critical (ToS). (c) DRAFT PURITY: the ban on em dashes (—), en dashes (–) and smart quotes (“ ” ‘ ’) applies to generated resume/cover-letter text - its source of truth is src/job_applier/ai/prompts/draft.md + ai/bans.py; flag a change that loosens either, or a drafting path that persists text without drafts.save_markdown = warning. Em dashes elsewhere are fine - do not flag them. (d) NO AUTOMATED ETHICS SCORING of companies (CLAUDE.md) = warning. (e) Preferences: a new user preference needs its key + default + bounds in contracts.py and the bounds on the Pydantic model in api/schemas.py, and every read parses defensively = warning. (f) No direct commits to main: the reviewed work came through a branch = critical otherwise.\n4. Commands are run through `uv run` (no activated-venv assumption) in Makefile targets and docs."
      },
      {
        "key": "error-handling",
        "category": "error-handling",
        "label": "Error Handling",
        "prompt": "A focused pass on swallowed errors, unhandled failure paths, and incomplete returns. Baseline concern - never fold into another agent.\n\n1. Fallible sinks in every changed file - confirm each result is checked or the exception deliberately surfaces: Python: httpx `.get(` / `.post(` / `raise_for_status()`, `pypdf.PdfReader(`, `pdf.render_to_pdf(` (raises PdfRendererUnavailable -> map to 503), `json.loads(`, `Path(...).read_text(`, SQLModel `.one()` (raises on miss), `subprocess.run(` (TimeoutExpired, non-zero returncode). TypeScript: `await request.formData()` / `request.json()` in actions, `fetch(` without `response.ok`. Flag unguarded use where failure corrupts state or 500s a request = warning (critical on ingest/dedupe/scoring persistence paths).\n2. Swallowed errors: bare `except:`, `except Exception: pass`, an except that logs and continues into inconsistent state; TS `catch {}` with no recovery. FALSE-POSITIVE GUARD: a broad `except Exception` carrying `# noqa: BLE001` plus a comment explaining the isolation (e.g. one source can't abort the run in run_ingest; one bad job can't kill a batch) is deliberate - do not flag it.\n3. HTTP mapping: endpoints map expected failures to deliberate statuses (404 missing row, 409 conflict, 422 validation, 503 renderer/lock); a lost SQLite lock race is mapped to 503 + Retry-After by the OperationalError handler in api/app.py - flag new code that catches OperationalError and hides it. SvelteKit form actions return `fail(<status>, {...})` on bad input or API errors, never throw = warning.\n4. Incomplete return paths: a declared return type whose branches fall through to implicit None/undefined = warning."
      },
      {
        "key": "correctness",
        "category": "correctness",
        "label": "Correctness & Caller-Impact",
        "prompt": "Generic runtime-bug hunting plus the highest-value real-bug finder: checking every caller of every changed function. Baseline concern - never drop it or merge it into a domain agent.\n\n1. Generic correctness bugs in every changed file: (a) nullable/sentinel results used unguarded - Python `dict.get(`, `re.search(`, `Session.get(Model, id)`, `.first()`, `next(iter, None)` -> None; TS `.find(` -> undefined, `.indexOf(` -> -1 = warning, critical on a crash/data-corruption path. (b) Off-by-one: slice/range ends, LIMIT/OFFSET, inclusive vs exclusive date windows and score thresholds. (c) Inverted/incorrect logic: wrong enum (FilterStatus.dropped vs .manual; ApplicationStatus rejected vs no_response vs archived), and/or precedence. (d) Timezone: naive vs aware datetime comparisons (the codebase normalizes with tzinfo=timezone.utc).\n2. Caller-impact analysis (do this thoroughly - it is the strongest bug finder). For EVERY function whose signature, return type/semantics, raised errors, persisted-row shape, or response shape changed in the diff: (a) grep the ENTIRE repo (src/job_applier/, web/src/, tests/, desktop/, .claude/commands/) for call sites and report the count; (b) verify each caller still passes correct args and handles the new return/None/error; (c) CROSS-LANGUAGE DRIFT: a changed endpoint request/response shape or enum must be mirrored in web/src/lib/api.ts types and every +page.server.ts loader/action using it; (d) flag callers that now break or silently misbehave as critical.\n3. Dangling references: renamed/removed functions, enum values, DB columns, setting keys, or task kinds still referenced anywhere = critical.\n4. Partial-state transitions: updating the active MatchScore without snapshotting MatchScoreHistory (services.upsert_score is the one path), changing Application.status without the transition bookkeeping (services.apply_status_transition: applied_at, next_followup_at) = warning."
      },
      {
        "key": "jsdoc",
        "category": "docs",
        "label": "Documentation (docstrings / TSDoc)",
        "prompt": "Both halves have a doc-comment convention (Python docstrings; TSDoc/JSDoc). There is NO pydocstyle / eslint-plugin-jsdoc enforcement - use that as the false-positive guard: never demand docs the project doesn't require. The value is catching STALE/WRONG docs.\n\n1. Tag-vs-behavior on every changed documented function: Python `Raises:` / 'Raises' text matches the real `raise` statements; `Returns:` matches the real return (incl. None on miss); `Args:` names match the signature; TSDoc `@param` / `@returns` / `@throws` likewise. Drift = warning (critical if a caller relies on it).\n2. Module docstrings / file lead-in comments state a module's responsibility (e.g. api.ts 'must stay browser-safe', ingest.py's pipeline description): if behavior changed, the lead-in must move with it = warning.\n3. Prompt/rubric sources of truth: src/job_applier/ai/prompts/*.md (+ ai/bans.py for the character list) are canonical for the scoring rubric and ATS/draft rules. .claude/commands/ (match-pending, score-draft, draft, suggest-roles) is the LEGACY Claude-Code path that must be mirrored AFTER ai/prompts/ changes. Flag a rubric/draft-rule change made only in .claude/commands/, or not mirrored there, as warning.\n4. CLAUDE.md / web/CLAUDE.md / README drift: a new make target, source adapter, status value, setting, or architectural rule not reflected there = suggestion (warning if CLAUDE.md now states something false).\n5. Public-surface presence (soft): new exported names in api/, services.py, web/src/lib/api.ts without a one-line doc = suggestion only."
      },
      {
        "key": "migrations",
        "category": "migrations",
        "label": "DB Migration & Schema Safety",
        "prompt": "No alembic: schema changes are idempotent helpers in src/job_applier/models/db.py run on EVERY startup from `create_db_and_tables()` (SQLModel.metadata.create_all(engine()) then each `_ensure_*` helper). tests/test_migrations.py builds a legacy DB from `_LEGACY_SCHEMA` and guards the pattern (parity, idempotency, `test_no_ensure_helper_is_orphaned`, `test_legacy_schema_covers_every_model_table`).\n\n1. For every changed SQLModel field added in models/db.py there is a matching `_ensure_*` helper that adds the column to an existing DB; missing = critical.\n2. Idempotency: every helper checks `PRAGMA table_info(<table>)` membership before `ALTER TABLE`; an unguarded ALTER (throws on the second startup) = critical. One-time backfills must be gated on the column being absent so they never re-run.\n3. Wiring: each new `_ensure_*` is called from `create_db_and_tables()` (the orphan test enforces this); a new table is added to `_LEGACY_SCHEMA` at its shipped shape; ordering respects dependencies (a helper that rebuilds tables runs after the column helpers it relies on).\n4. SQLite limits: ADD COLUMN of a NOT NULL column needs a DEFAULT. Dropping or retyping a column, or dropping an inline UNIQUE, needs a table rebuild. A DELIBERATE rebuild (rename aside -> create from the SQLModel metadata -> copy -> drop, in one transaction, gated on a column check) or `ALTER TABLE ... RENAME COLUMN` is ACCEPTABLE when tests/test_migrations.py seeds real rows and asserts they survive. Flag ONLY unguarded or untested rebuilds/renames, or ad-hoc destructive SQL, as critical.\n5. Backfill correctness: a backfill copying from another column must COALESCE legacy NULLs into a NOT NULL target; a backfill assigning ownership must not re-home rows on later startups = critical.\n6. No alembic / migration logic outside models/db.py = warning.\n7. Prune/dedupe safety (maintenance.py): `make prune` blanks description/raw but NEVER deletes rows (`test_prune_never_deletes_rows`) and preserves `dedupe_hash`, `cross_source_hash`, `jd_fingerprint`, `duplicate_of`; violations = critical.",
        "conditional": true,
        "requiresAny": [
          "schema"
        ]
      },
      {
        "key": "sveltekit",
        "category": "sveltekit",
        "label": "SvelteKit Frontend Conventions",
        "prompt": "Only run the substantive checks if the diff touches web/ - otherwise return no findings. The user is learning SvelteKit here and wants idiomatic patterns; convention drift is worth flagging even when it 'works'.\n\n1. Mutations via form actions: writes (status changes, saves, deletes) go through `actions` in +page.server.ts, never client `fetch(` from a .svelte file = warning. (Exception, per web/CLAUDE.md: the Cmd/Ctrl-K palette's typeahead GET to /api/search.)\n2. Loaders, not onMount fetching: initial page data comes from `load` in +page.server.ts / +layout.server.ts = warning otherwise.\n3. api.ts must stay browser-safe: grep web/src/lib/api.ts and its imports for `$env/dynamic/private`, `$env/static/private`, `node:`, `fs`, `path` = critical. Server-only helpers live in *.server.ts (e.g. apiBase.server.ts).\n4. Typed client: backend calls go through a typed method on the `api` object in api.ts with a matching TypeScript type - no inline fetch + any = warning.\n5. Form-action contract: actions validate input and return `fail(<status>, { ... })` on bad input or API errors rather than throwing (see jobs/[id]/+page.server.ts) = warning.\n6. Svelte 5 runes: `$state` / `$derived` / `$effect` and `$props()`; cross-route state in a *.svelte.ts rune store (like draftCart.svelte.ts); `$app/state` not `$app/stores`. Legacy `writable`/`readable` stores or `export let` in new code = suggestion. An `$effect` that both reads and writes the same state (effect_update_depth_exceeded) = warning.\n7. Design system (web/CLAUDE.md): colors use the tokens in src/app.css (and back-compat aliases), not hardcoded hex; score bands come from web/src/lib/score.ts / ScoreBadge, never hardcoded 80/65 thresholds = suggestion (warning for the score thresholds).",
        "conditional": true,
        "requiresAny": [
          "web"
        ]
      },
      {
        "key": "ai-safety",
        "category": "security",
        "label": "AI Sandbox & Untrusted-Input Safety",
        "prompt": "Job descriptions are untrusted scraped text and model output is treated as data. CLAUDE.md defines the choke points; this agent keeps them intact.\n\n1. Provider sandbox (src/job_applier/ai/providers.py): every CLI call is `subprocess.run(` with an argv list - grep for `shell=True` = critical; a `timeout=` on every call = critical if missing; the child env comes from `_scrubbed_env()` (API keys stripped) = critical if bypassed; the cwd is a throwaway `tempfile.TemporaryDirectory`; tool use stays disabled (the empty `--allowed-tools` for Claude Code) = critical if loosened.\n2. No SDKs or keys: `import anthropic` / `from anthropic` / `import openai` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` anywhere under src/ = critical. Server-side AI through the CLI subprocess is the intended design - do not flag it.\n3. Untrusted input into prompts: job/resume text reaches a prompt only through the nonce fence - `prompt_safety.new_nonce()` + `clean_untrusted(...)` / the ai/templates renderers. Raw f-string interpolation of a description into a prompt = critical.\n4. Model output as data: responses are parsed into Pydantic models / JSON envelopes, never passed to eval/exec/subprocess or used as a file path = critical. Scores are range-checked (0-100) before persisting.\n5. Draft exfiltration: every draft markdown write goes through `drafts.save_markdown` (which applies `bans.sanitize` and `bans.strip_exfil_vectors`), and drafting refuses to persist text where `bans.find_banned` still finds characters. A new write path that bypasses them = critical.\n6. PDF engine blocks subresources: src/job_applier/pdf.py aborts every non-navigation request (`_permit_request` / `route.abort()`), and desktop/main.js does the same in the print session's `webRequest.onBeforeRequest`. Loosening either (allowing images/fonts/remote URLs, enabling JS) = critical - drafts are sent to employers.\n7. Legacy path parity: .claude/commands/*.md mirror ai/prompts/; a safety rule added to one and not the other = warning.",
        "conditional": true,
        "requiresAny": [
          "ai"
        ]
      }
    ]
  },
  "codebase-audit": {
    "mode": "audit",
    "projectName": "job-applier",
    "defaultBranch": "main",
    "testCommand": "make test",
    "categoryOrder": [
      "architecture",
      "security",
      "migrations",
      "correctness",
      "error-handling",
      "tests",
      "sveltekit",
      "dry",
      "docs",
      "best-practices"
    ],
    "verdictLabels": {
      "fail": "UNHEALTHY",
      "warn": "NEEDS WORK",
      "pass": "HEALTHY"
    },
    "warningThreshold": 5,
    "mergeThreshold": 8,
    "preamble": "You are auditing the current state of job-applier: a Python 3.12 FastAPI + SQLModel (SQLite) backend with a typer CLI under src/job_applier/, a SvelteKit 2 + Svelte 5 (runes) + TypeScript frontend under web/src/, and an Electron desktop shell under desktop/. This is not a diff review: examine all relevant source files. Work through every item in the checklist below, reading the relevant code for each. When an item says to grep or search, run the search and base the result on its output. Aim for repeatable results: the same code should yield the same findings. Read CLAUDE.md (and web/CLAUDE.md for frontend files) and honor its conventions. Report each issue via the structured schema (an item that passes produces no finding); severity must be one of critical, warning, suggestion.",
    "scopeInstructions": "Examine ALL source files (not a diff). Enumerate with: `find src/job_applier -name '*.py'`, `find web/src \\( -name '*.ts' -o -name '*.svelte' \\) -not -name '*.test.ts'`, and `find desktop -maxdepth 1 -name '*.js'`. Backend tests live in tests/ (test_<module>.py); frontend tests are colocated *.test.ts / page.svelte.test.ts (used by the Test-Quality dimension for coverage mapping). Do NOT flag web/node_modules/, web/.svelte-kit/, web/build/, desktop/dist/, desktop/node_modules/, data/, applications/, *.lock, package-lock.json. If the user narrowed the run, the engine appends a Narrowed scope section listing the paths to examine.",
    "agents": [
      {
        "key": "test-quality",
        "category": "tests",
        "label": "Test Quality & Coverage",
        "prompt": "Conventions: backend pytest under tests/ (test_<module>.py; the `make_raw` RawJob factory in tests/conftest.py; in-memory SQLite via create_engine(\"sqlite://\", poolclass=StaticPool) plus app.dependency_overrides[get_session] for API tests; @pytest.mark.parametrize for table cases). Frontend vitest + @testing-library/svelte, colocated: web/src/lib/*.test.ts, web/src/lib/__tests__/, and page.svelte.test.ts beside a route's +page.svelte. Run with `make test`.\n\nCALIBRATE FOR GOOD TESTS, NOT COVERAGE: a missing test is a finding ONLY when real logic could ship a bug without it - never because 'everything should have a test'. A low-value/padding test (asserts nothing real) is itself a finding, to trim. For a dedicated, deeper test pass, point the user at /test-quality-audit if it is installed.\n\n1. For every source file under src/job_applier/ and web/src/ (exclude generated files and *.d.ts): (a) find its tests - backend: tests/test_<module>.py or any tests/ file importing the module/function (grep the name); frontend: the colocated *.test.ts / page.svelte.test.ts - and report whether they exist; (b) if they exist, verify the non-trivial BEHAVIORS (branches, error paths, state transitions, HTTP status codes) are tested - NOT that every symbol has a test; (c) if none exist, flag ONLY logic-bearing code where a regression would ship a bug: ingest/dedupe/filters/matching, scoring persistence, migrations, form actions = critical; other real branching = warning. Do NOT flag thin routers that only delegate to services, Pydantic/SQLModel field declarations, pure constants, or presentational .svelte markup.\n2. Read every test file: (a) assertions test OUTCOMES (rows written, response JSON + status code, rendered text), not implementation; (b) flag tests that only assert `is not None` / `toBeTruthy()` / 'does not raise', plus tautological or over-mocked tests that assert nothing real - recommend trimming or rewriting them; (c) DATA SAFETY: a test that enters `TestClient(app)` (which runs the app lifespan and so create_db_and_tables()) or calls `engine()` / `Session(engine())` without the DB path isolated (an autouse isolation fixture in tests/conftest.py, or monkeypatching settings.db_path / models.db._engine to a temp DB) = critical: it migrates and writes the developer's real data/jobs.db; (d) a test stubbing a function the code now calls with a new keyword argument (e.g. a `lambda kind, total, fn, ref=None:` stub for tasks.start_task) must accept it.\n3. Branch coverage on core systems (ingest.py, filters/rules.py, services.py, models/db.py migrations, the task runner, web/src/lib/api.ts): sample-check that error and state-transition branches have tests. Untested ones = warning.\n4. Mock hygiene: grep tests for `monkeypatch.setattr(` and `vi.mock(` / `vi.fn(`; verify each stubbed callable's signature still matches the real one. Stale stubs = warning."
      },
      {
        "key": "dry",
        "category": "dry",
        "label": "DRY Code",
        "prompt": "1. Read every source file (.py under src/job_applier/, .ts / .svelte under web/src/, .js under desktop/; not tests). For each: (a) a code block (3+ lines) repeated more than once in the same file = warning; (b) a block nearly identical to code in another source file = warning.\n2. For each pattern found, grep the full repo (src/job_applier/, web/src/, desktop/) for ALL occurrences and report count + locations. Flag only if it appears 3+ times OR extraction meaningfully reduces maintenance burden.\n3. Repeated literals used as setting keys, status names, task kinds, route paths, or query params across files - flag values appearing 3+ times. Setting keys and bounds belong in src/job_applier/contracts.py; status/facet values must come from the Python enums or web/src/lib/api.ts constants (APPLICATION_STATUSES).\n4. Shared frontend abstractions already exist (taskRunner.svelte.ts, jobActions.server.ts, StatusTrackingCard, JobDescription): flag new code re-implementing one of them.\n5. Do NOT flag: test verbosity, SQLModel/Pydantic field repetition, or two-site patterns in clearly different contexts."
      },
      {
        "key": "architecture",
        "category": "architecture",
        "label": "Architecture",
        "prompt": "Layering (CLAUDE.md): routers in src/job_applier/api/ map HTTP <-> DTOs (api/schemas.py, api/serializers.py); src/job_applier/services.py is the shared persistence layer used by both the HTTP routes and the AI orchestrator and must NOT import job_applier.api; contracts.py holds shared keys/defaults/bounds; models/db.py is the schema; source adapters implement the SourceAdapter protocol (sources/base.py) and are registered in sources/__init__.py; the AI layer lives in src/job_applier/ai/. Frontend: web/src/lib/api.ts is the typed client; cross-route state lives in *.svelte.ts rune stores; the desktop shell in web/src/lib/shell/.\n\n1. For every module: (a) God object - LOC, function count, distinct responsibilities; flag modules past ~400 lines that mix concerns (HTTP + persistence + business rules) as warning, past ~700 or 3+ concerns as critical; report the top 3 largest/most-dense modules in metrics.largestModules (file + loc). (b) Duplicated source of truth: state held in two places (e.g. a preference stored both in AppSetting and on SearchProfile, a status mirrored in two tables) = critical. (c) Dependency direction: grep for `from job_applier.api` / `import job_applier.api` inside services.py, ingest.py, ai/, sources/, models/ = critical (the application layer must not depend on the web edge).\n2. NEVER HOLD A DB TRANSACTION ACROSS NETWORK OR SUBPROCESS I/O (CLAUDE.md): a `with Session(...)` / open session that spans `source.fetch()`, httpx calls, `subprocess.run`, or a provider call blocks every other SQLite writer for its whole duration. Flag as critical. Writes must be batched into short transactions (see run_ingest / INGEST_BATCH_SIZE).\n3. Data-shape choice: table row -> SQLModel in models/db.py; request/response -> Pydantic in api/schemas.py; cross-module constants/keys/bounds -> contracts.py; transient -> dataclass. Flag misuse (e.g. returning ORM rows from an endpoint, DTOs in services.py) as warning.\n4. New source adapters: implement SourceAdapter, registered in sources/__init__.py, per-company ones read slugs from SourceSlug at runtime (not hard-coded). Flag drift as warning.\n5. Long work runs as a background task (ai/tasks.py start_task, polled/streamed by the UI), never inside a request handler. Flag a new synchronous endpoint doing multi-second work as warning."
      },
      {
        "key": "best-practices",
        "category": "best-practices",
        "label": "Python / TypeScript Best Practices",
        "prompt": "1. Python - for every .py file under src/job_applier/: (a) public functions have typed params and return types; modules start with `from __future__ import annotations` - missing = warning. (b) No bare `except:`; no `except Exception` that silently swallows (Error Handling covers semantics; flag the syntax here). (c) No `print(` in src/job_applier/ - the CLI (cli.py) uses typer.echo, everything else logging = warning. (d) f-strings over %-formatting / .format() in new code = suggestion. (e) Would `make lint` (ruff check src/) flag it: unused imports/variables, import order.\n2. TypeScript / Svelte - for every .ts / .svelte file under web/src/: (a) exported functions have explicit types; no `any` leaking into the typed api surface = warning. (b) `import type` for type-only imports. (c) No `console.log(` in committed code - grep web/src/ = warning. (d) Must pass `cd web && npm run check` (svelte-check).\n3. Repo hard rules (carry verbatim - these catch more than 'follow conventions'): (a) NO AI SDKs OR API KEYS: grep for `import anthropic`, `from anthropic`, `import openai`, `from openai`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` = critical. AI runs server-side ONLY by shelling out to a user-installed CLI via src/job_applier/ai/providers.py; that is allowed. (b) NO LINKEDIN / INDEED: grep src/job_applier/sources/ for `linkedin.com` / `indeed.com` = critical (ToS). (c) DRAFT PURITY: the ban on em dashes (—), en dashes (–) and smart quotes (“ ” ‘ ’) applies to generated resume/cover-letter text - its source of truth is src/job_applier/ai/prompts/draft.md + ai/bans.py; flag a change that loosens either, or a drafting path that persists text without drafts.save_markdown = warning. Em dashes elsewhere are fine - do not flag them. (d) NO AUTOMATED ETHICS SCORING of companies (CLAUDE.md) = warning. (e) Preferences: a new user preference needs its key + default + bounds in contracts.py and the bounds on the Pydantic model in api/schemas.py, and every read parses defensively = warning. (f) No direct commits to main: the reviewed work came through a branch = critical otherwise.\n4. Commands are run through `uv run` (no activated-venv assumption) in Makefile targets and docs."
      },
      {
        "key": "error-handling",
        "category": "error-handling",
        "label": "Error Handling",
        "prompt": "A focused pass on swallowed errors, unhandled failure paths, and incomplete returns. Baseline concern - never fold into another agent.\n\n1. Fallible sinks in every file - confirm each result is checked or the exception deliberately surfaces: Python: httpx `.get(` / `.post(` / `raise_for_status()`, `pypdf.PdfReader(`, `pdf.render_to_pdf(` (raises PdfRendererUnavailable -> map to 503), `json.loads(`, `Path(...).read_text(`, SQLModel `.one()` (raises on miss), `subprocess.run(` (TimeoutExpired, non-zero returncode). TypeScript: `await request.formData()` / `request.json()` in actions, `fetch(` without `response.ok`. Flag unguarded use where failure corrupts state or 500s a request = warning (critical on ingest/dedupe/scoring persistence paths).\n2. Swallowed errors: bare `except:`, `except Exception: pass`, an except that logs and continues into inconsistent state; TS `catch {}` with no recovery. FALSE-POSITIVE GUARD: a broad `except Exception` carrying `# noqa: BLE001` plus a comment explaining the isolation (e.g. one source can't abort the run in run_ingest; one bad job can't kill a batch) is deliberate - do not flag it.\n3. HTTP mapping: endpoints map expected failures to deliberate statuses (404 missing row, 409 conflict, 422 validation, 503 renderer/lock); a lost SQLite lock race is mapped to 503 + Retry-After by the OperationalError handler in api/app.py - flag new code that catches OperationalError and hides it. SvelteKit form actions return `fail(<status>, {...})` on bad input or API errors, never throw = warning.\n4. Incomplete return paths: a declared return type whose branches fall through to implicit None/undefined = warning."
      },
      {
        "key": "correctness",
        "category": "correctness",
        "label": "Correctness & Call-Site Consistency",
        "prompt": "Generic runtime-bug hunting plus call-site consistency across the codebase. Baseline concern - never drop it or merge it into a domain agent.\n\n1. Generic correctness bugs in every file: (a) nullable/sentinel results used unguarded - Python `dict.get(`, `re.search(`, `Session.get(Model, id)`, `.first()`, `next(iter, None)` -> None; TS `.find(` -> undefined, `.indexOf(` -> -1 = warning, critical on a crash/data-corruption path. (b) Off-by-one: slice/range ends, LIMIT/OFFSET, inclusive vs exclusive date windows and score thresholds. (c) Inverted/incorrect logic: wrong enum (FilterStatus.dropped vs .manual; ApplicationStatus rejected vs no_response vs archived), and/or precedence. (d) Timezone: naive vs aware datetime comparisons (the codebase normalizes with tzinfo=timezone.utc).\n2. Call-site consistency: for each public function in services.py, ingest.py, filters/, the api.ts client methods, and every API endpoint: (a) grep all call sites (src/job_applier/, web/src/, tests/, desktop/); (b) verify each passes correct args and handles the return shape, None, and errors consistently; (c) CROSS-LANGUAGE DRIFT: Python enums/DTOs vs the types and constants in web/src/lib/api.ts must match - flag mismatches as critical.\n3. Dangling references: renamed/removed functions, enum values, DB columns, setting keys, or task kinds still referenced anywhere = critical.\n4. Partial-state transitions: updating the active MatchScore without snapshotting MatchScoreHistory (services.upsert_score is the one path), changing Application.status without the transition bookkeeping (services.apply_status_transition: applied_at, next_followup_at) = warning."
      },
      {
        "key": "jsdoc",
        "category": "docs",
        "label": "Documentation (docstrings / TSDoc)",
        "prompt": "Both halves have a doc-comment convention (Python docstrings; TSDoc/JSDoc). There is NO pydocstyle / eslint-plugin-jsdoc enforcement - use that as the false-positive guard: never demand docs the project doesn't require. The value is catching STALE/WRONG docs.\n\n1. Tag-vs-behavior on every documented function: Python `Raises:` / 'Raises' text matches the real `raise` statements; `Returns:` matches the real return (incl. None on miss); `Args:` names match the signature; TSDoc `@param` / `@returns` / `@throws` likewise. Drift = warning (critical if a caller relies on it).\n2. Module docstrings / file lead-in comments state a module's responsibility (e.g. api.ts 'must stay browser-safe', ingest.py's pipeline description): if behavior changed, the lead-in must move with it = warning.\n3. Prompt/rubric sources of truth: src/job_applier/ai/prompts/*.md (+ ai/bans.py for the character list) are canonical for the scoring rubric and ATS/draft rules. .claude/commands/ (match-pending, score-draft, draft, suggest-roles) is the LEGACY Claude-Code path that must be mirrored AFTER ai/prompts/ changes. Flag a rubric/draft-rule change made only in .claude/commands/, or not mirrored there, as warning.\n4. CLAUDE.md / web/CLAUDE.md / README drift: a new make target, source adapter, status value, setting, or architectural rule not reflected there = suggestion (warning if CLAUDE.md now states something false).\n5. Public-surface presence (soft): new exported names in api/, services.py, web/src/lib/api.ts without a one-line doc = suggestion only. Report the share of documented public functions in metrics.jsdocCoverage."
      },
      {
        "key": "migrations",
        "category": "migrations",
        "label": "DB Migration & Schema Safety",
        "prompt": "No alembic: schema changes are idempotent helpers in src/job_applier/models/db.py run on EVERY startup from `create_db_and_tables()` (SQLModel.metadata.create_all(engine()) then each `_ensure_*` helper). tests/test_migrations.py builds a legacy DB from `_LEGACY_SCHEMA` and guards the pattern (parity, idempotency, `test_no_ensure_helper_is_orphaned`, `test_legacy_schema_covers_every_model_table`).\n\n1. For every SQLModel field added in models/db.py there is a matching `_ensure_*` helper that adds the column to an existing DB; missing = critical.\n2. Idempotency: every helper checks `PRAGMA table_info(<table>)` membership before `ALTER TABLE`; an unguarded ALTER (throws on the second startup) = critical. One-time backfills must be gated on the column being absent so they never re-run.\n3. Wiring: each new `_ensure_*` is called from `create_db_and_tables()` (the orphan test enforces this); a new table is added to `_LEGACY_SCHEMA` at its shipped shape; ordering respects dependencies (a helper that rebuilds tables runs after the column helpers it relies on).\n4. SQLite limits: ADD COLUMN of a NOT NULL column needs a DEFAULT. Dropping or retyping a column, or dropping an inline UNIQUE, needs a table rebuild. A DELIBERATE rebuild (rename aside -> create from the SQLModel metadata -> copy -> drop, in one transaction, gated on a column check) or `ALTER TABLE ... RENAME COLUMN` is ACCEPTABLE when tests/test_migrations.py seeds real rows and asserts they survive. Flag ONLY unguarded or untested rebuilds/renames, or ad-hoc destructive SQL, as critical.\n5. Backfill correctness: a backfill copying from another column must COALESCE legacy NULLs into a NOT NULL target; a backfill assigning ownership must not re-home rows on later startups = critical.\n6. No alembic / migration logic outside models/db.py = warning.\n7. Prune/dedupe safety (maintenance.py): `make prune` blanks description/raw but NEVER deletes rows (`test_prune_never_deletes_rows`) and preserves `dedupe_hash`, `cross_source_hash`, `jd_fingerprint`, `duplicate_of`; violations = critical."
      },
      {
        "key": "sveltekit",
        "category": "sveltekit",
        "label": "SvelteKit Frontend Conventions",
        "prompt": "The user is learning SvelteKit here and wants idiomatic patterns; convention drift is worth flagging even when it 'works'.\n\n1. Mutations via form actions: writes (status changes, saves, deletes) go through `actions` in +page.server.ts, never client `fetch(` from a .svelte file = warning. (Exception, per web/CLAUDE.md: the Cmd/Ctrl-K palette's typeahead GET to /api/search.)\n2. Loaders, not onMount fetching: initial page data comes from `load` in +page.server.ts / +layout.server.ts = warning otherwise.\n3. api.ts must stay browser-safe: grep web/src/lib/api.ts and its imports for `$env/dynamic/private`, `$env/static/private`, `node:`, `fs`, `path` = critical. Server-only helpers live in *.server.ts (e.g. apiBase.server.ts).\n4. Typed client: backend calls go through a typed method on the `api` object in api.ts with a matching TypeScript type - no inline fetch + any = warning.\n5. Form-action contract: actions validate input and return `fail(<status>, { ... })` on bad input or API errors rather than throwing (see jobs/[id]/+page.server.ts) = warning.\n6. Svelte 5 runes: `$state` / `$derived` / `$effect` and `$props()`; cross-route state in a *.svelte.ts rune store (like draftCart.svelte.ts); `$app/state` not `$app/stores`. Legacy `writable`/`readable` stores or `export let` in new code = suggestion. An `$effect` that both reads and writes the same state (effect_update_depth_exceeded) = warning.\n7. Design system (web/CLAUDE.md): colors use the tokens in src/app.css (and back-compat aliases), not hardcoded hex; score bands come from web/src/lib/score.ts / ScoreBadge, never hardcoded 80/65 thresholds = suggestion (warning for the score thresholds)."
      },
      {
        "key": "ai-safety",
        "category": "security",
        "label": "AI Sandbox & Untrusted-Input Safety",
        "prompt": "Job descriptions are untrusted scraped text and model output is treated as data. CLAUDE.md defines the choke points; this agent keeps them intact.\n\n1. Provider sandbox (src/job_applier/ai/providers.py): every CLI call is `subprocess.run(` with an argv list - grep for `shell=True` = critical; a `timeout=` on every call = critical if missing; the child env comes from `_scrubbed_env()` (API keys stripped) = critical if bypassed; the cwd is a throwaway `tempfile.TemporaryDirectory`; tool use stays disabled (the empty `--allowed-tools` for Claude Code) = critical if loosened.\n2. No SDKs or keys: `import anthropic` / `from anthropic` / `import openai` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` anywhere under src/ = critical. Server-side AI through the CLI subprocess is the intended design - do not flag it.\n3. Untrusted input into prompts: job/resume text reaches a prompt only through the nonce fence - `prompt_safety.new_nonce()` + `clean_untrusted(...)` / the ai/templates renderers. Raw f-string interpolation of a description into a prompt = critical.\n4. Model output as data: responses are parsed into Pydantic models / JSON envelopes, never passed to eval/exec/subprocess or used as a file path = critical. Scores are range-checked (0-100) before persisting.\n5. Draft exfiltration: every draft markdown write goes through `drafts.save_markdown` (which applies `bans.sanitize` and `bans.strip_exfil_vectors`), and drafting refuses to persist text where `bans.find_banned` still finds characters. A new write path that bypasses them = critical.\n6. PDF engine blocks subresources: src/job_applier/pdf.py aborts every non-navigation request (`_permit_request` / `route.abort()`), and desktop/main.js does the same in the print session's `webRequest.onBeforeRequest`. Loosening either (allowing images/fonts/remote URLs, enabling JS) = critical - drafts are sent to employers.\n7. Legacy path parity: .claude/commands/*.md mirror ai/prompts/; a safety rule added to one and not the other = warning."
      }
    ]
  }
}
/* <<< END GENERATED CONFIGS <<< */

// ---------------------------------------------------------------------------
// `args` carries ONLY small runtime scope: { configKey, scope: { branch, date,
// summary, present?, focus?, precomputed? } }. See the header for why the config does
// NOT travel through args. Some harness paths hand `args` over as a JSON
// string, so normalize — and if that parse fails, say plainly that the payload
// was truncated rather than surfacing a bare SyntaxError.
// ---------------------------------------------------------------------------
let parsedArgs
if (typeof args === 'string') {
  try {
    parsedArgs = JSON.parse(args)
  } catch (err) {
    throw new Error(
      'review-engine: `args` arrived as a string and failed to parse, which ' +
        'almost always means the harness TRUNCATED it in transit. Received ' +
        `${args.length} chars ending \`${args.slice(-60)}\`. Pass only small ` +
        'runtime scope in args ({ configKey, scope }); the per-project config ' +
        'belongs in PROJECT_CONFIGS inside this file. ' +
        `Underlying parse error: ${err.message}`,
    )
  }
} else {
  parsedArgs = args || {}
}

const scope = parsedArgs.scope || {}

// `scope.focus` narrows a run to the paths the user asked about (e.g. "just audit
// auth" → ['src/features/auth/']). The config's scopeInstructions stay as the
// baseline; focus is layered on top of them in every agent prompt. A malformed
// focus throws rather than being ignored, because ignoring it would silently
// widen the run back to the whole codebase.
let focus = null
if (scope.focus !== undefined && scope.focus !== null) {
  const raw = typeof scope.focus === 'string' ? [scope.focus] : scope.focus
  if (!Array.isArray(raw) || raw.length === 0 || !raw.every((p) => typeof p === 'string' && p.trim())) {
    throw new Error(
      'review-engine: scope.focus must be a non-empty list of repo-relative paths or globs ' +
        `(received ${JSON.stringify(scope.focus)}). Omit it to run the full configured scope.`,
    )
  }
  focus = raw.map((p) => p.trim())
}
const configKeys = Object.keys(PROJECT_CONFIGS)

// Resolution order:
//   1. An explicit configKey MUST name a PROJECT_CONFIGS entry. Never fall back
//      from an unknown key — with one config generated, that would silently run
//      a different skill's agents (e.g. /test-quality-audit running code-review).
//   2. No configKey: a legacy `args.config` (launchers emitted before configs
//      moved into this file, and throwaway prototypes) is used as-is.
//   3. No configKey and no config: a single generated config is unambiguous.
let cfg
if (parsedArgs.configKey) {
  if (!Object.prototype.hasOwnProperty.call(PROJECT_CONFIGS, parsedArgs.configKey)) {
    throw new Error(
      `review-engine: configKey "${parsedArgs.configKey}" has no entry in PROJECT_CONFIGS ` +
        `(holds: ${configKeys.length > 0 ? configKeys.join(', ') : '(none generated)'}). ` +
        'Re-run generate-project-skills (or update-project-skills) to add this ' +
        "launcher's config to the engine — refusing to fall back to another skill's config.",
    )
  }
  cfg = PROJECT_CONFIGS[parsedArgs.configKey]
} else if (parsedArgs.config) {
  cfg = parsedArgs.config
} else if (configKeys.length === 1) {
  cfg = PROJECT_CONFIGS[configKeys[0]]
}

if (!cfg || !Array.isArray(cfg.agents) || cfg.agents.length === 0) {
  const known = configKeys.length > 0 ? configKeys.join(', ') : '(none generated)'
  throw new Error(
    'review-engine: could not resolve a config with a non-empty agents[]. ' +
      `Requested configKey=${parsedArgs.configKey ?? '(unset)'}; ` +
      `PROJECT_CONFIGS holds: ${known}. ` +
      `Received args of type=${typeof args}, ` +
      `parsed keys=${parsedArgs && typeof parsedArgs === 'object' ? Object.keys(parsedArgs).join(',') : '(n/a)'}.` +
      (configKeys.length === 0
        ? ' The GENERATED CONFIGS slot is still empty — the generator did not fill it in.'
        : ''),
  )
}

// One findings schema, reused by every audit agent. severity is a hard enum so
// the model is forced to classify; file+description are required; line/fix are
// optional. `metrics` lets the few agents that compute coverage ratios report
// them in a structured slot instead of burying them in prose.
const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      description: 'Every issue found for this audit dimension. Empty array if the dimension passes clean.',
      items: {
        type: 'object',
        required: ['severity', 'file', 'description'],
        properties: {
          severity: { type: 'string', enum: ['critical', 'warning', 'suggestion'] },
          category: { type: 'string', description: 'Leave blank; the orchestrator stamps it from the agent.' },
          file: { type: 'string', description: 'Repo-relative path, e.g. apps/web/app/lib/recipes.server.ts' },
          line: { type: 'string', description: 'Line number or range, e.g. "42" or "42-51". Optional.' },
          description: { type: 'string', description: 'Concrete, actionable description of the issue.' },
          fix: { type: 'string', description: 'Suggested fix or code snippet. Optional.' },
        },
      },
    },
    metrics: {
      type: 'object',
      description: 'Optional structured coverage metrics for the report header. Only the relevant agent fills each field.',
      properties: {
        jsdocCoverage: { type: 'string' },
        storybookCoverageWeb: { type: 'string' },
        storybookCoverageMobile: { type: 'string' },
        largestModules: {
          type: 'array',
          items: {
            type: 'object',
            properties: { file: { type: 'string' }, loc: { type: 'number' } },
          },
        },
      },
    },
  },
}

// The semantic-merge agent returns the deduped, contradiction-resolved set.
// Naive dedup-by-file:line is dumber than the model: it keeps near-dupes that
// two dimensions worded differently. So we hand the model the full set and let
// it merge by underlying issue, keep the highest severity + most actionable
// description, and resolve contradictions.
const MERGE_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'category', 'file', 'description'],
        properties: {
          severity: { type: 'string', enum: ['critical', 'warning', 'suggestion'] },
          category: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'string' },
          description: { type: 'string' },
          fix: { type: 'string' },
          mergedFrom: {
            type: 'array',
            description: 'Categories/dimensions this finding was consolidated from, when >1 agent flagged it.',
            items: { type: 'string' },
          },
        },
      },
    },
  },
}

const modeLabel = cfg.mode === 'diff' ? 'branch-diff review' : 'full-codebase audit'

// COST LEVER 1 — deterministic-conditional domain agents. A domain agent may be
// gated on file-presence signals the launcher precomputed in `scope.present`
// (e.g. ['ui','network','auth']); it runs only when at least one of its
// `requiresAny` signals is present. This is DETERMINISTIC (same scope → same
// agents) and FAIL-OPEN: a conditional agent is skipped ONLY when the launcher
// positively classified the diff (non-empty `scope.present`) and the agent's
// signal wasn't among the classifications. If `scope.present` is omitted OR
// empty — i.e. the diff couldn't be classified, possibly because the launcher's
// patterns are incomplete — every agent runs, so a pattern gap never silently
// drops a domain agent. Baseline agents are never marked conditional, so the
// six-baseline guarantee is untouched. Skipped agents are logged + returned.
const present = Array.isArray(scope.present) && scope.present.length > 0 ? scope.present : null
const isActive = (a) => {
  if (!a.conditional || !Array.isArray(a.requiresAny) || a.requiresAny.length === 0) return true
  if (!present) return true
  return a.requiresAny.some((sig) => present.includes(sig))
}
const activeAgents = cfg.agents.filter(isActive)
const skippedAgents = cfg.agents.filter((a) => !isActive(a))

log(
  `${modeLabel} of ${cfg.projectName}: ${activeAgents.length}/${cfg.agents.length} agents over ` +
    `${scope.summary || 'the configured source roots'} (branch ${scope.branch}, ${scope.date})`,
)
if (focus) log(`Scope narrowed to: ${focus.join(', ')}`)
if (skippedAgents.length) {
  log(
    `Skipped ${skippedAgents.length} conditional domain agent(s) — required files absent from scope: ` +
      skippedAgents.map((a) => `${a.key}(needs ${a.requiresAny.join('/')})`).join(', '),
  )
}

// --- Phase 1: deterministic fan-out -----------------------------------------
// parallel() is the RIGHT barrier here: the semantic-merge step genuinely needs
// ALL findings at once to dedup across dimensions. The fan-out fires every run
// for every ACTIVE agent — the model cannot silently spawn fewer.
//
// COST LEVER 2 — per-agent model/effort tiering. Each agent config entry may set
// `model` (e.g. a cheaper tier for mechanical pattern-matching agents) and/or
// `effort`; when absent the agent inherits the session default. Tier ASSIGNMENT
// lives in the emitted config, not here — the engine only plumbs it through.
phase('Review')
const raw = await parallel(
  activeAgents.map((a) => () =>
    agent(
      [
        cfg.preamble,
        '',
        '## Scope — the files you must examine',
        cfg.scopeInstructions,
        ...(focus
          ? [
              '',
              '## Narrowed scope',
              'The user narrowed this run. Within the scope above, examine only files under these paths, ' +
                'plus the tests that cover them wherever the project keeps its tests (beside the source or ' +
                'in a separate test tree), and report findings only for those files. Caller-impact, call-site ' +
                'and dangling-reference checks may still grep the whole repo:',
              ...focus.map((p) => `- ${p}`),
            ]
          : []),
        '',
        `## Your audit dimension: ${a.label}`,
        a.prompt,
        '',
        'Return each issue you found as a structured finding. Checklist items that pass produce no finding; a clean dimension returns an empty findings array. ' +
          'Set file to a repo-relative path and include a line number/range whenever you can pinpoint one. ' +
          'Leave the `category` field blank — the orchestrator stamps it.',
      ].join('\n'),
      {
        label: `review:${a.key}`,
        phase: 'Review',
        schema: FINDINGS_SCHEMA,
        ...(a.model ? { model: a.model } : {}),
        ...(a.effort ? { effort: a.effort } : {}),
      },
    ).then((r) => ({ key: a.key, category: a.category, result: r })),
  ),
)

// Stamp category from the agent config (deterministic prioritization) and
// collect every finding into one flat list. Also gather any structured metrics.
const collected = []
const metrics = { ...(scope.precomputed || {}) }
for (const item of raw) {
  if (!item || !item.result) continue
  for (const f of item.result.findings || []) {
    collected.push({ ...f, category: item.category, source: item.key })
  }
  if (item.result.metrics) {
    for (const [k, v] of Object.entries(item.result.metrics)) {
      if (v !== undefined && v !== null && !(Array.isArray(v) && v.length === 0)) metrics[k] = v
    }
  }
}
// Track agents that died (API/rate-limit error, timeout) — parallel() resolves
// a failed thunk to null, so a failed agent contributes no findings. Without
// surfacing this, a run where every agent died looks identical to a clean run
// (0 findings → HEALTHY/PASS). That false-pass is dangerous, so we count
// failures and downgrade the verdict to an explicit INCOMPLETE when the run is
// too degraded to trust.
const failedAgentKeys = raw
  .map((x, i) => (x && x.result ? null : activeAgents[i] && activeAgents[i].key))
  .filter(Boolean)
const failedAgents = failedAgentKeys.length
log(
  `Collected ${collected.length} raw findings across ${activeAgents.length - failedAgents}/${activeAgents.length} ` +
    `dimensions${failedAgents ? ` (${failedAgents} agent(s) FAILED: ${failedAgentKeys.join(', ')})` : ''}; merging.`,
)

// --- Phase 2: merge ----------------------------------------------------------
// COST LEVER 3 — gate the semantic-merge AGENT on a finding-count threshold.
// Below cfg.mergeThreshold we collapse exact duplicates in JS instead of paying
// for a model call. EXCEPTION: diff mode (cfg.mergeExtraInstructions set) ALWAYS
// uses the agent — it carries the pre-existing-vs-new downgrade JS can't do.
// Default threshold 0 ⇒ always agent-merge (behavior unchanged unless config opts in).
phase('Merge')
let findings = collected
const mergeThreshold = cfg.mergeThreshold ?? 0
const useAgentMerge =
  collected.length > 0 && (!!cfg.mergeExtraInstructions || collected.length >= mergeThreshold)
if (useAgentMerge) {
  const mergeLines = [
    `You are the synthesis step of a ${modeLabel}. Below is the full set of ${collected.length} raw findings`,
    'produced by independent review agents, as JSON. Your job:',
    '1. SEMANTIC dedup: merge findings that describe the SAME underlying issue, even when worded differently,',
    '   reported at slightly different lines, or flagged by more than one dimension. Keep the HIGHEST severity,',
    '   the MOST actionable description, and the MOST specific file:line. Record the merged categories in mergedFrom.',
    '2. Do NOT drop genuinely distinct issues. When unsure whether two findings are the same, keep both.',
    '3. Contradiction check: if two findings contradict (e.g. "remove X" vs "X is missing"), resolve using context',
    '   and keep the correct one.',
    '4. Preserve each finding\'s category (one of: ' + cfg.categoryOrder.join(', ') + ').',
  ]
  // mode:'diff' adds the pre-existing-vs-new downgrade rule (code-review only).
  if (cfg.mergeExtraInstructions) mergeLines.push(cfg.mergeExtraInstructions)
  mergeLines.push('', 'Raw findings JSON:', JSON.stringify(collected))
  const merged = await agent(mergeLines.join('\n'), {
    label: 'merge:semantic-dedup',
    phase: 'Merge',
    schema: MERGE_SCHEMA,
    ...(cfg.mergeModel ? { model: cfg.mergeModel } : {}),
    ...(cfg.mergeEffort ? { effort: cfg.mergeEffort } : {}),
  })
  if (merged && Array.isArray(merged.findings) && merged.findings.length > 0) {
    findings = merged.findings
  }
} else if (collected.length > 0) {
  // Cheap exact dedup: collapse findings sharing file + line + category + the
  // head of the description. Conservative — only true duplicates collapse, so
  // we never silently drop a distinct issue the way aggressive matching might.
  const seen = new Map()
  for (const f of collected) {
    const k = `${f.file}::${f.line || ''}::${f.category}::${(f.description || '').slice(0, 60)}`
    if (!seen.has(k)) seen.set(k, f)
  }
  findings = [...seen.values()]
  log(
    `Merge agent skipped (${collected.length} findings < threshold ${mergeThreshold}); ` +
      `JS exact-dedup → ${findings.length}.`,
  )
}

// --- Verdict + sort in JS (deterministic) -----------------------------------
const sevRank = { critical: 0, warning: 1, suggestion: 2 }
const order = cfg.categoryOrder
const catRank = (c) => {
  const i = order.indexOf(c)
  return i === -1 ? order.length : i
}
findings.sort(
  (a, b) => (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9) || catRank(a.category) - catRank(b.category),
)

const counts = {
  critical: findings.filter((f) => f.severity === 'critical').length,
  warning: findings.filter((f) => f.severity === 'warning').length,
  suggestion: findings.filter((f) => f.severity === 'suggestion').length,
}
// Verdict labels + warning threshold are config-driven so the SAME engine
// serves both modes: audit → HEALTHY/NEEDS WORK/UNHEALTHY at >5 warnings;
// diff (code-review) → PASS/NEEDS WORK/BLOCK at >3 warnings. Defaults = audit.
const labels = cfg.verdictLabels || { fail: 'UNHEALTHY', warn: 'NEEDS WORK', pass: 'HEALTHY' }
const warningThreshold = cfg.warningThreshold ?? 5
// A run is too degraded to trust when every agent failed (no real analysis ran)
// — never report a pass in that case. A partial failure still yields a verdict
// but is flagged via `degraded`/`failedAgents` so the launcher can warn the user.
const allFailed = activeAgents.length > 0 && failedAgents === activeAgents.length
const verdict = allFailed
  ? 'INCOMPLETE — all agents failed (no analysis ran)'
  : counts.critical > 0
    ? labels.fail
    : counts.warning > warningThreshold
      ? labels.warn
      : labels.pass

// Top 3 categories of concern by weighted finding count (critical=3, warn=2, sug=1)
const weight = { critical: 3, warning: 2, suggestion: 1 }
const byCat = {}
for (const f of findings) byCat[f.category] = (byCat[f.category] || 0) + (weight[f.severity] || 1)
const topConcerns = Object.entries(byCat)
  .sort((a, b) => b[1] - a[1])
  .slice(0, 3)
  .map(([c]) => c)

return {
  mode: cfg.mode || 'audit',
  project: cfg.projectName,
  branch: scope.branch,
  date: scope.date,
  focus,
  agentCount: activeAgents.length,
  configuredAgents: cfg.agents.length,
  skippedAgents: skippedAgents.length,
  skippedAgentKeys: skippedAgents.map((a) => a.key),
  failedAgents,
  failedAgentKeys,
  degraded: failedAgents > 0,
  rawFindingCount: collected.length,
  findings,
  counts,
  verdict,
  topConcerns,
  metrics,
}
