---
name: codebase-audit
description: Perform a full-codebase audit of this Python (FastAPI/SQLModel) + SvelteKit project. Analyzes best practices, unit tests, DRY code, architecture, error handling, correctness & caller-impact, documentation, DB migration safety, and SvelteKit conventions across ALL source files — not limited to a branch diff. Use when the user wants to audit the whole project, evaluate overall code quality, or review the full codebase.
---

# codebase-audit

Comprehensive audit of the entire codebase as it exists right now. Unlike `/code-review` (scoped to a branch diff), this skill evaluates ALL source files to surface issues in the current state of the project.

This is a mixed-stack repo: a Python 3.12 backend (FastAPI, SQLModel over SQLite, a `typer` CLI exposed as `job-applier`) under `src/job_applier/`, and a SvelteKit frontend (Svelte 5 runes, TypeScript) under `web/src/`.

## When to Use
- User wants to audit the full project
- User wants a quality assessment not scoped to a PR
- User merged work without running `/code-review` and wants to catch issues
- User invokes `/codebase-audit`

## Instructions

### Step 1: Identify Scope

Enumerate all source files. Exclude caches, build output, and dependencies:

```bash
# Backend source
find src/job_applier -type f -name '*.py' -not -path '*/__pycache__/*' | sort

# Frontend source
find web/src -type f \( -name '*.ts' -o -name '*.svelte' \) -not -name '*.test.ts' | sort

# Backend tests (used by Agent 1 for coverage mapping)
find tests -type f -name 'test_*.py' | sort

# Frontend tests
find web/src -type f -name '*.test.ts' | sort
```

If the user specifies a narrower scope (e.g., "just audit the source adapters" or "just the frontend"), respect it.

### Step 2: Spawn Parallel Audit Agents

Launch **nine** agents in parallel using the Agent tool (subagent_type: `general-purpose`). Each agent examines ALL relevant source files, not just recently changed ones.

**Critical preamble — include in every agent prompt:**

> You are performing an exhaustive audit of a Python (FastAPI / SQLModel / typer) + SvelteKit (TypeScript, Svelte 5 runes) project's current state. This is NOT a diff review — you must examine ALL relevant source files. You must check EVERY item in the checklist below — do not skip items or stop early. For each item, read the relevant code and report either a finding or "PASS". When an item asks you to grep or search, you MUST actually run the search and report the results — do not assume. Your audit must be deterministic: given the same code, you should always find the same issues.

**Each agent returns a structured report with:**
- Severity: `critical`, `warning`, or `suggestion`
- File path and line number (or range)
- Clear description
- Suggested fix or code snippet when appropriate

---

#### Agent 1: Test Quality & Coverage

1. **Read [CLAUDE.md](../../../CLAUDE.md)** — test conventions: backend `pytest` in `tests/test_<name>.py`, fixtures from `tests/conftest.py` (`make_raw` `RawJob` factory), `@pytest.mark.parametrize` for table-driven cases; frontend `vitest` in `web/src/lib/*.test.ts` or `__tests__/`, using `@testing-library/svelte`. Command: `make test`.

2. **Enumerate every source module** under `src/job_applier/` and the testable frontend helpers under `web/src/lib/`. For EACH:
   a. Search for a matching test (`tests/test_<module>.py`; colocated `*.test.ts`). Report existence.
   b. If it exists, read it. Verify every public function, API endpoint, filter rule, source adapter, and exported helper has at least one test.
   c. If NO test exists, flag per user rule ("always write tests"): core logic (filters, ingest/dedupe, scoring history, source adapters, migrations) = `critical`; API routes / frontend stores = `warning`; thin wrapper / presenter = `suggestion`.

3. **Read every test file.** For each test:
   a. Assertions test **outcomes** (`FilterResult.status`, persisted rows, response bodies, store state), not implementation details.
   b. Flag tests that only assert "is not None" / "does not raise".
   c. Flag backend tests that hand-roll a `RawJob` where the `make_raw` factory would do.
   d. Flag filter-rule areas missing the pass/drop parametrized symmetry the existing suite maintains.
   e. Frontend: flag tests that don't exercise the runes-driven state transition they claim to cover.

4. **Branch coverage:** for key systems (filter `rules.py`, `ingest.py` dedupe layers, scoring/history, each source adapter) sample-check that `if` / `match` / early-return branches have tests. Untested filter outcomes (`passed` / `manual` / `dropped`) or error branches = `warning`.

5. **Source-adapter coverage:** every adapter in `src/job_applier/sources/` should have at least one parse/fetch test (see `tests/test_sources.py`). Flag adapters with no test as `warning`.

---

#### Agent 2: DRY Code

1. **Read all source files** under `src/job_applier/` and `web/src/`. For each file:
   a. Code blocks (3+ lines) repeated more than once in the same file → `warning`.
   b. Blocks nearly identical to code in another source file → `warning`.

2. **For each pattern found, grep the full repo** to find ALL occurrences. Report count + locations. Flag only if it appears 3+ times OR extraction meaningfully reduces maintenance burden.

3. **Repeated literals** used as source names, filter reasons, status strings, API paths, or DB column names across files — flag magic values appearing 3+ times; recommend a `const`/enum or the shared types in `web/src/lib/api.ts`.

4. **Source-adapter duplication:** grep the adapters in `src/job_applier/sources/` for repeated fetch/parse/normalize boilerplate that should live in `base.py`. Recommend extraction if 3+ adapters copy the same shape.

5. **Cross-layer duplication:** the same enum/status set should not be defined independently in Python (`models/db.py`) and TypeScript (`web/src/lib/api.ts`). They will drift — note divergence as `warning` (acknowledging there's no shared codegen).

6. **Do NOT flag:** test verbosity, trivial property accessors, or two-site patterns in clearly different contexts.

---

#### Agent 3: Architecture

User is a senior engineer who values cohesive boundaries and dislikes god objects and shared-state desync. Weight findings accordingly.

1. **Read [CLAUDE.md](../../../CLAUDE.md)** — layering: FastAPI app + schemas in `api/`; frontend talks only through `web/src/lib/api.ts`; adapters implement the `SourceAdapter` protocol and register in `sources/__init__.py`; hard filter in `filters/rules.py`; schema + migrations in `models/db.py`.

2. **God object check** across every module:
   a. Lines of code, public-function count, distinct responsibilities.
   b. Flag any module over ~400 lines OR mixing clearly distinct concerns (HTTP fetch + parse + persist in one place, etc.) as `warning`. Over ~700 lines or 3+ distinct concerns → `critical`.
   c. Call out the top 3 largest / most-responsibility-dense modules regardless — the user wants proactive architecture signal.

3. **Shared-state desync:** is any data held in two places? The DB owns scores, dedupe hashes, and application status. Grep for parallel caches/state that duplicate a DB-owned value. Flag duplicated sources of truth as `critical`. Confirm score writes always snapshot to `MatchScoreHistory` rather than overwriting silently.

4. **Layer integrity:**
   a. Does the frontend reach the backend anywhere except through the typed `api` client in `api.ts`? Flag raw cross-origin `fetch` as `warning`. (Browser-safety of `api.ts` itself is covered by Agent 9.)
   b. **LLM boundary:** grep the entire backend for `import anthropic`, `from anthropic`, `ANTHROPIC_API_KEY`, or any server-side model call. Scoring/drafting must only happen via `.claude/commands/` slash commands. Any server-side LLM call is `critical`.

5. **Source-adapter conformance:** every adapter in `sources/` must implement the `SourceAdapter` protocol and be registered in `sources/__init__.py`; per-company adapters read slugs from the `SourceSlug` table at runtime (`companies.py` is seed-only). Flag deviations.

6. **Right shape for data:** backend — `SQLModel` table vs Pydantic schema vs dataclass (`RawJob`). Frontend — rune store (`*.svelte.ts`) vs loader data vs component-local state. Flag misuse (e.g. cross-route state in a component instead of a rune store like `draftCart.svelte.ts`).

7. **Feature cohesion:** are files in the correct package (`api/`, `sources/`, `filters/`, `models/`) and route folder? Flag misplacement as `suggestion`.

---

#### Agent 4: Python / TypeScript Best Practices

1. **Read [CLAUDE.md](../../../CLAUDE.md)** for conventions. Lint is `make lint` (`ruff check src/`); frontend typecheck is `cd web && npm run check`.

2. **For every `.py` source file:**
   a. **Type hints:** public functions typed; modules use `from __future__ import annotations`. Report the count of untyped public functions and flag the worst offenders as `warning`.
   b. **No bare `except:`** / silent `except Exception` swallowing — grep `src/job_applier/` for `^[[:space:]]*except:` and `except Exception:` followed by no body / `pass`. Flag as `warning`. (Deep error semantics → Agent 5.)
   c. **Leftover `print()`** in library code: grep `src/job_applier/` for `print(`; flag each (prefer `typer.echo` in CLI, logging elsewhere) as `warning`.
   d. **f-strings** over `%`/`.format()`. `suggestion`.
   e. **Ruff cleanliness:** flag unused imports, unsorted imports, unused variables.

3. **For every `.ts` / `.svelte` file:**
   a. **Explicit types** on exported functions; grep for `: any` and `as any` leaking into the `api` surface. Flag as `warning`.
   b. **`import type`** for type-only imports.
   c. **Leftover `console.log`:** grep `web/src/` and flag each as `warning`.
   d. Should pass `npm run check` (svelte-check). Flag obvious type errors.

4. **Repo-specific hard rules:**
   a. **No `anthropic` SDK / API-key handling server-side** — grep all `.py` files. `critical`. (Also covered by Agent 3 — keep both since this is a hard project rule.)
   b. **No LinkedIn / Indeed scraping** — grep all source for `linkedin.com` / `indeed.com` scraping endpoints. ToS violation. `critical`.
   c. **Generated-draft purity:** review `.claude/commands/draft.md` and any draft-rendering code paths for em dashes (`—`), en dashes (`–`), or smart quotes (`"` `"` `'` `'`) in rules for generated output. `warning`.
   d. **Branch check:** confirm there is no in-progress work committed directly to `main` that bypassed a PR. Compare `git log main` against recent feature-branch merges. Flag non-merge commits on main as `warning`.

---

#### Agent 5: Error Handling

A focused sweep for swallowed errors, unhandled failure paths, and incomplete returns across the whole codebase. This is a baseline concern — never fold it into another agent.

1. **Read [CLAUDE.md](../../../CLAUDE.md)** for error-handling / logging conventions.

2. **Fallible sinks — grep and audit across every source file:**
   - Python: `httpx` calls, `Response.raise_for_status`, `pypdf.PdfReader(...)`, `pdf.render_to_pdf(...)` (headless Chromium/Electron), `json.loads(...)`, `Path(...).read_text(...)`, `Session.exec(...).one()`, subprocess calls. Report unguarded sinks; the worst offenders (data paths in `ingest.py`, `drafts.py`, source adapters) → `critical`.
   - TypeScript / SvelteKit: `await request.json()` / `request.formData()` in actions without try/catch, `fetch(...)` without `.ok` checks, loaders/actions with implicit `undefined` returns. Report locations; `warning`.

3. **Swallowed errors:**
   - Grep `src/job_applier/` for `except:` (bare), `except Exception: pass`, `except Exception:` blocks that log and fall through into invalid state.
   - Grep `web/src/` for bare `catch {}` and `catch (e) { console.log(e) }` with no re-throw or recovery.
   Report counts and worst offenders. `warning` per occurrence, `critical` when the swallow corrupts persisted state.

4. **Async/await failure paths:**
   - Python `async def` endpoints that `await` without try/catch when downstream raises should map to a deliberate HTTP status, not a 500 + silent log.
   - SvelteKit form actions returning `fail(...)` on bad input vs throwing — survey new actions and flag throwers.

5. **Incomplete return paths:** functions with a declared return type whose branches fall through to implicit `None`/`undefined`. `warning`.

---

#### Agent 6: Correctness & Caller-Impact

Generic runtime-bug hunting plus call-site consistency across the codebase. This is a baseline concern — never drop it, never merge it into a domain agent.

1. **Generic correctness bugs across all source files:**
   a. **Nullable / sentinel lookups used unguarded:**
      - Python: `dict.get(key)` → `None`, `re.search(...)` → `None`, `Session.get(Model, id)` → `None`, `next(iter, default)` patterns. Dereference without a `None` guard → `warning`, or `critical` on a crash/data-corruption path.
      - TypeScript: `Array.prototype.find()` → `undefined`, `.indexOf()` → `-1`, optional-chaining gaps.
   b. **Off-by-one:** loop bounds, `len(x) - 1`, range/slice endpoints, SQL `LIMIT`/`OFFSET`, score thresholds, date ranges.
   c. **Inverted / incorrect boolean logic:** wrong negation, `and`/`or` precedence, comparison against the wrong constant or enum value (`FilterStatus` / `ApplicationStatus` mismatches between Python and TS).
   d. **Type confusion masked by a cast:** Python casts on `dict.get(...)` to non-Optional, TS `as` hiding `undefined`.

2. **Call-site consistency for public API.** For every public function in `src/job_applier/api/`, every method on a `SourceAdapter`, every exported helper in `web/src/lib/api.ts`, and every form action:
   a. Grep all call sites across `src/job_applier/`, `web/src/`, and `tests/`.
   b. Verify every caller passes correct args, handles the return type/shape, and accounts for error paths or `None`/`undefined` results consistently.
   c. **Cross-language drift:** for each backend endpoint, confirm the typed `api` client in `web/src/lib/api.ts` and every consuming `+page.server.ts` use the same request/response shape. Flag drift as `critical`.

3. **Dangling references:** grep for references to functions, enum values, DB columns, or constants that no longer exist → `critical`.

4. **Partial-state transitions:** code paths that leave a row with some fields updated and others stale (e.g. `MatchScore` updates that skip `MatchScoreHistory`, `Application.status` changes that skip `last_contact_at`) → `warning`.

---

#### Agent 7: Documentation

Audit the whole codebase for stale, drifted, or out-of-sync docs. Both halves have a doc-comment convention (Python docstrings; JSDoc/TSDoc), but neither half has lint-enforced presence requirements — so the high-value finds are correctness, not coverage.

1. **Read [CLAUDE.md](../../../CLAUDE.md)** for documentation rules. No `pydocstyle` / `eslint-plugin-jsdoc` is configured — use that as a false-positive guard: don't demand docs the linter exempts.

2. **Tag-vs-behavior cross-checks across every public function with a doc comment:**
   a. Python `Raises:` / "Raises" lines must match the actual `raise` statements. Flag drift (`Raises: ValueError` on a function that raises `HTTPException`).
   b. Python `Returns:` text must match the actual return type/shape (including `None` paths).
   c. Python `Args:` / `:param:` names/types must match the signature.
   d. TSDoc / JSDoc `@param` / `@returns` / `@throws` in `web/src/` — same drift checks.

3. **Module / file lead-ins:** modules carrying a "what this is for" docstring or top-comment (e.g. the `web/src/lib/api.ts` "must stay browser-safe" comment) — confirm the lead-in still matches the module's actual responsibility. Flag drift.

4. **Coverage ratio on the public seam:** report (do not flag) the proportion of exported names in `src/job_applier/api/` and `web/src/lib/api.ts` that have any doc comment. `suggestion` if the ratio is unusually low for a publicly-shaped module — never `warning` (no project rule mandates docs).

5. **Slash-command rubric sync:** `.claude/commands/match-pending.md` and `.claude/commands/score-draft.md` share a rubric and MUST stay in sync (CLAUDE.md rule). Diff the relevant sections; flag drift as `warning`. Same for `draft.md` chain-calls to `score-draft`.

6. **README / CLAUDE.md drift:** if the codebase has make targets, source adapters, status states, or env vars not mentioned in `README.md` / `CLAUDE.md`, list the gaps as `suggestion`.

---

#### Agent 8: DB Migration & Schema Safety

The project deliberately uses no alembic. Schema changes are hand-rolled idempotent `_ensure_*_columns()` helpers in `src/job_applier/models/db.py`, run on EVERY startup from `init_db()`. `make prune` lightens old rows but must preserve dedupe hashes. Audit the whole schema-migration surface for divergence risk.

1. **Read [src/job_applier/models/db.py](../../../src/job_applier/models/db.py)** in full — `init_db()` and every `_ensure_*_columns()` helper. Understand the pattern: read columns via `PRAGMA table_info(<table>)`, then `ALTER TABLE ... ADD COLUMN` only for missing ones.

2. **Column/helper parity:** for every `SQLModel` table, cross-check each declared field against the `_ensure_*` helpers and the base table create. Any field that exists on the model but has neither a base-table definition guaranteed for fresh installs nor an `_ensure_*` migration for existing DBs is a divergence risk → `critical`.

3. **Idempotency:** every helper must guard with a `PRAGMA table_info` membership check before `ALTER TABLE`. Flag any unconditional `ALTER TABLE` as `critical`.

4. **Wiring:** confirm every `_ensure_*` helper is called from `init_db()`. Flag orphaned (uncalled) helpers as `critical`.

5. **SQLite limits:** flag any drop/rename/retype via raw SQL, or any `NOT NULL` column added without a default (existing rows violate it), as `critical`.

6. **No alembic / auto-migrate creep:** confirm there is no alembic and no migration logic living outside `db.py`. Flag any found.

7. **Prune/dedupe safety:** read the `prune` and JD-dedupe code paths; confirm dedupe hash columns (`cross_source_hash`, `jd_fingerprint`) and `duplicate_of` links are never cleared. Flag data loss as `critical`.

---

#### Agent 9: SvelteKit Frontend Conventions

The user is learning SvelteKit here and wants idiomatic patterns. Sweep every file under `web/src/`.

1. **Read [CLAUDE.md](../../../CLAUDE.md)** frontend section and [web/src/lib/api.ts](../../../web/src/lib/api.ts) for the established client shape.

2. **Mutations via form actions:** every write (status change, notes, draft render) must go through SvelteKit form `actions` in `+page.server.ts`, NOT client-side `fetch`. Grep all `.svelte` files for mutation `fetch(` calls. Flag as `warning`.

3. **Loaders, not onMount fetching:** page data must come from `load` in `+page.server.ts`. Grep for `onMount` doing initial data fetches. Flag as `warning`.

4. **`api.ts` browser-safety:** grep `web/src/lib/api.ts` (and its imports) for `$env/dynamic/private`, `$env/static/private`, `node:`, `fs`, `path`, or any server-only import — it is imported by browser code. Flag as `critical`.

5. **Typed client discipline:** every backend call should go through a typed method on the `api` object with a matching TypeScript type. Flag ad-hoc inline `fetch` + `any` as `warning`.

6. **Svelte 5 runes:** cross-route shared state belongs in a rune store (`*.svelte.ts`). Audit for legacy Svelte 4 stores (`writable`/`readable`) where runes (`$state`/`$derived`) are idiomatic. Flag legacy patterns as `suggestion`.

7. **Form-action validation:** every action should validate input and return `fail(...)` on bad input (see the `VALID_STATUS` guard in `web/src/routes/jobs/[id]/+page.server.ts`). Flag unvalidated actions as `warning`.

---

### Step 3: Compile & Present Results

**Deduplication:** multiple agents may flag the same issue. Keep the highest severity and most actionable description. Do NOT list the same issue twice.

**Contradiction check:** scan for contradictions (e.g., "remove X" vs "X is missing"). Resolve using full context.

**Prioritization:** within each severity, sort by category in this order: architecture > correctness > migrations > error-handling > tests > sveltekit > docs > DRY > best-practices.

```
## Codebase Audit: [branch] / [date]

### Critical Issues
(Must fix — architecture breakage, schema/data desync, missing tests on core systems)
- [category] file:line — description

### Warnings
(Should fix — maintainability, coverage gaps, convention drift)
- [category] file:line — description

### Suggestions
(Nice to have — polish, consistency)
- [category] file:line — description

### Summary
- X critical, Y warnings, Z suggestions
- Overall: HEALTHY / NEEDS WORK / UNHEALTHY
- Top 3 areas of concern: [list]
- Largest modules (god-object risk): [top 3 with LOC]
```

**Verdict logic:**
- Any `critical` finding → **UNHEALTHY**
- More than 5 `warning` findings → **NEEDS WORK**
- Otherwise → **HEALTHY**

### Step 4: Offer Remediation

After presenting the report, ask the user if they'd like:
1. A prioritized remediation plan, or
2. Help fixing specific findings, starting with criticals.

If many findings exist, recommend landing fixes in small, themed PRs rather than one mega-branch.
