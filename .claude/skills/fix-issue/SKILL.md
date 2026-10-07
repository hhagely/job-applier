---
name: fix-issue
description: Pick up an open GitHub issue in this repo and ship a fix as a PR. Reads the issue body (assumes the create-issue format), branches from latest main, implements the change, runs `make test`, pushes, and opens a PR that closes the issue. Use when the user asks to fix/work/pick up an issue or invokes /fix-issue.
---

# fix-issue

Take a single issue from open to PR. The issue is the source of truth — read it, plan against it, verify against its acceptance criteria. Issues filed by [create-issue](../create-issue/SKILL.md) follow a known shape: Summary / Context / Current Behavior / Expected Behavior / Acceptance Criteria / (optional) Notes.

## When to Use

- User asks to "fix issue #N", "pick up the latest issue", "work on issue X"
- User invokes `/fix-issue` with or without a number
- User asks what's available — list open `claude-ready` issues

## Arguments

- `/fix-issue 42` — fix that specific issue
- `/fix-issue` (no arg) — list open `claude-ready` issues and ask which to pick up
- `/fix-issue list` — same as no-arg

Never pick an issue on your own without showing the user the candidate first.

## Instructions

### Step 1: Resolve the target issue

If given a number, fetch it:

```bash
gh issue view <N> --json number,title,body,labels,state,url
```

If no number, list candidates:

```bash
gh issue list --state open --label claude-ready --json number,title,labels --limit 20
```

Show the list and ask which one. If the chosen issue is closed, stop and ask.

### Step 2: Read context

Parse the issue body against the [create-issue](../create-issue/SKILL.md) template (Summary / Context / Current Behavior / Expected Behavior / Acceptance Criteria). If the format is off, work from what's there — don't refuse to proceed, but flag the gap to the user.

Read [CLAUDE.md](../../../CLAUDE.md) for project conventions (Python 3.12 + FastAPI + SQLModel + `typer` CLI under `src/job_applier/`; SvelteKit + Svelte 5 runes + TypeScript under `web/src/`; pytest + vitest, `make test`; ruff lint via `make lint`; migrations as `_ensure_*_columns()` in `models/migrations.py` — no alembic; mutations via SvelteKit form actions; `web/src/lib/api.ts` must stay browser-safe; AI only via the sandboxed CLI subprocess in `ai/providers.py`, never an SDK or API key; no LinkedIn/Indeed scraping; feature branch + PR workflow).

Read the files named in **Context**. If the issue doesn't name files, grep/find them from the description before guessing (`grep -rn` across `src/job_applier/` and `web/src/`).

### Step 3: Plan and confirm

Output to the user:

- 1–2 sentences restating what the issue asks for
- Files you intend to touch (Python under `src/job_applier/`, frontend under `web/src/`, tests under `tests/` and/or `web/src/**`)
- Test plan — which existing tests cover this area, which new test(s) you'll add (pytest for backend, vitest for frontend)
- Any ambiguity in the issue you want resolved before coding

Wait for go-ahead. This step exists because issue bodies are often terse and one round of clarification saves a wrong-direction PR.

### Step 4: Branch from latest main

```bash
git status --short                  # must be clean
git fetch origin main
git checkout main && git pull --ff-only origin main
git checkout -b fix/issue-<N>-<short-slug>
```

If the working tree is dirty, stop and ask. Never auto-stash.

### Step 5: Implement + test

- Write the fix in scope of the acceptance criteria.
- Add or update tests. Backend tests go in `tests/test_<module>.py` (reuse the `make_raw` factory from `conftest.py` for `RawJob` setup; use `@pytest.mark.parametrize` for table-driven cases). Frontend tests go colocated as `web/src/lib/*.test.ts` or under `web/src/lib/__tests__/` (use `@testing-library/svelte`). New modules / endpoints / source adapters need tests (project rule).
- If the change adds a `SQLModel` field, also add a matching idempotent `_ensure_*_columns()` helper in `src/job_applier/models/migrations.py` and wire it into `migrations.run()` — fresh and returning DBs both need to land on the same schema.
- Run `make test` (which runs both `make test-api` pytest and `make test-web` vitest). If backend changed, also run `make lint`. If frontend changed, also run `cd web && npm run check`.
- Iterate until green. Don't push a red branch.

### Step 6: Standards self-review

Before staging, re-verify the diff against the project's standards — not just that it works, but that it meets the bar `/code-review` would hold it to. Run the gate against the same diff you're about to commit:

```bash
git diff main
```

Walk this checklist; each line is a gate, not a suggestion. Fix anything that fails before committing.

- **Python / TypeScript idioms** — Python: typed params and returns on public functions, `from __future__ import annotations` at the top of new modules, no bare `except:`, f-strings, ruff-clean (`make lint`). TypeScript/Svelte: explicit types on exports, no `any` leaking into `web/src/lib/api.ts`, `import type` for type-only imports, `npm run check` clean.
- **Logging** — no `print(` in `src/job_applier/` (the CLI uses `typer.echo`, everything else logging); no `console.log(` in `web/src/`. Grep the diff for both.
- **Error handling** — check fallible calls: `httpx` `.get(` / `.post(` / `raise_for_status()`, `json.loads(`, `Path(...).read_text(`, SQLModel `.one()`, `subprocess.run(` (timeouts, non-zero exit), `pdf.render_to_pdf(` (→ 503). Form actions return `fail(<status>, { … })` instead of throwing; `fetch(` results check `response.ok`. A broad `except Exception` is acceptable only with `# noqa: BLE001` and a comment explaining the isolation — otherwise don't swallow errors.
- **Project hard rules** — grep the diff for `import anthropic`, `from anthropic`, `import openai`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (AI runs only through the CLI subprocess in `ai/providers.py`); `linkedin.com` / `indeed.com` in `src/job_applier/sources/`; and any loosening of the draft character ban (`—` `–` `“` `”` `‘` `’`) in `ai/prompts/draft.md` or `ai/bans.py`. Confirm you're not on `main`.
- **Never hold a DB transaction across network or subprocess I/O** — no open `Session` spanning `source.fetch()`, an `httpx` call, or `subprocess.run`; write in short batches.
- **Documentation** — where a changed function has a docstring or TSDoc, its `Raises:` / `Returns:` / `Args:` (or `@param` / `@returns` / `@throws`) still match the code. There is no `pydocstyle` / `eslint-plugin-jsdoc`, so don't add docs for their own sake. If the change alters a rubric or draft rule, edit `src/job_applier/ai/prompts/` first and then mirror it into the legacy `.claude/commands/`. Update `CLAUDE.md` if a rule it states is now false.
- **DB migrations** (when `models/` changed) — every new field has an `_ensure_*` helper in `models/migrations.py` guarded by `PRAGMA table_info`, called from `migrations.run()`; a new table is added to `_LEGACY_SCHEMA` in `tests/test_migrations.py`; a table rebuild or `RENAME COLUMN` is gated on a column check and covered by a migration test that seeds real rows; one-time backfills can't re-run; `make prune` still never deletes rows.
- **SvelteKit** (when `web/` changed) — mutations go through form actions in `+page.server.ts`, not client `fetch(`; page data comes from `load`, not `onMount`; `web/src/lib/api.ts` imports nothing from `$env/dynamic/private`, `$env/static/private`, `node:`, or `fs`; new backend calls are typed methods on `api`; Svelte 5 runes (`$state` / `$derived`, `$app/state`), and no `$effect` that reads and writes the same state.
- **AI safety** (when `ai/`, `drafts.py`, `pdf.py`, or `desktop/main.js` changed) — provider calls stay argv-only (`shell=True` never), with `timeout=` and `_scrubbed_env()`; untrusted job/resume text reaches prompts only via `prompt_safety.new_nonce()` / `clean_untrusted()`; model output is parsed as data; draft writes go through `drafts.save_markdown`; the PDF engines still abort every subresource (`_permit_request` / `route.abort()`, `webRequest.onBeforeRequest`).
- **Test data safety** — no new test enters `TestClient(app)` or calls `engine()` without an isolated DB path; the developer's real `data/jobs.db` must never be touched by `make test`.
- **No debris** — no commented-out code, no leftover scaffolding, no issue/PR references in comments.

`/code-review` is the deeper net and can be run on the branch as this gate (or in addition). Keep this inline checklist as the default: it's lighter and self-contained.

### Step 7: Commit, push, open PR

Match recent commit-message style (`git log --oneline -10`) — short, imperative, no trailing period.

```bash
git push -u origin HEAD

gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
<1–3 bullets on what changed>

## Test Plan
- [x] make test (passes locally)
- [ ] <any manual verification the user should do>

Closes #<N>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

The `Closes #<N>` line auto-closes the issue when the PR merges.

### Step 8: Report

Return the PR URL. One line.

## Guardrails

- **One issue per invocation.** Don't bundle fixes for multiple issues into a single PR even if they look related — open separate PRs and let the user merge in the order they prefer.
- **Stay in scope.** The acceptance criteria define done. If you spot adjacent bugs while reading the code, mention them at the end of the PR body (for a follow-up issue) but do not fix them in this PR.
- **Never push to main directly.** Always feature branch → PR.
- **Never close the issue manually.** Let the PR close it on merge.
- **Standards self-review (Step 6) is a gate, not a formality.** Run it against the diff before staging; fix what fails. Green tests are not the same as meeting the project's standards.
- **Hard project rules to never violate while fixing:**
  - No `anthropic` / `openai` SDK and no API-key handling (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). AI features run server-side **only** by shelling out to a user-installed CLI through the sandbox in `src/job_applier/ai/providers.py` (argv-only, scrubbed env, timeout, no tools). The `.claude/commands/` slash commands are a legacy mirror of `src/job_applier/ai/prompts/`, which is the source of truth.
  - No LinkedIn / Indeed scraping in source adapters.
  - No `print()` in `src/job_applier/` library code (use `typer.echo` in CLI, logging elsewhere); no `console.log` in committed frontend code.
  - No raw client `fetch` for mutations — use SvelteKit form actions in `+page.server.ts`.
  - `web/src/lib/api.ts` must stay browser-safe (no `$env/dynamic/private`, no `node:`/`fs` imports).
