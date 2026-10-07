---
name: code-review
description: Perform a full code review of the currently checked out branch against its base — the open PR's base branch (including an integration branch), an explicit base, or main — for this Python (FastAPI/SQLModel) + SvelteKit app. Analyzes tests, DRY code, architecture, Python/TypeScript best practices, error handling, correctness & caller-impact, documentation, DB migration safety, SvelteKit conventions, and AI-sandbox safety across the branch diff. Use when the user asks to review the branch, review a PR, or do a code review.
argument-hint: "[base-branch | PR number]"
---

# code-review

Perform a comprehensive code review of all changes on the currently checked out branch compared to its **base**: the branch its PR targets (often an integration branch, not `main`), a base the user names, or `main` when neither applies. Scope is limited to the branch diff — for a whole-codebase audit, use `/codebase-audit` instead.

This skill is a **thin launcher** for the shared review engine — a Claude Code Workflow at `.claude/skills/_shared/review-engine.workflow.js` (the SAME engine `/codebase-audit` uses, run with `mode: "diff"`). The engine fans out one agent per review dimension (deterministically — every dimension runs every time), each returns schema-enforced findings, then the engine semantically merges them and computes the verdict in JS. The verbose per-agent reports stay OUT of this conversation. **Auto-fix is NOT part of the workflow** — the engine returns findings only; you (the main loop) run the auto-fix in Step 4. Your job: (1) identify the diff inline, (2) call the Workflow with the review config, (3) present the result, (4) auto-fix on BLOCK/NEEDS WORK, then commit the fixes and push them to the branch, (5) final report.

## When to Use
- User asks to review the current branch or an open PR
- User asks for a code review before merging
- User invokes `/code-review`, optionally with a base branch (`/code-review desktop-app`) or a PR number (`/code-review 123`)

## Instructions

### Step 1: Identify Changes (inline, in this conversation)

**Resolve the base first.** Never assume `main`: a PR opened against an integration branch (this repo uses the long-lived `desktop-app` branch, one PR per phase) must be reviewed against that branch, or the review sweeps in every change the integration branch already holds. Take the first rule that applies:

1. **The user named a base** (`/code-review desktop-app`, "review against release/v0.3.0") — use it.
2. **The user gave a PR number or URL** — read its base and head with `gh pr view <n> --json baseRefName,headRefName`. If `headRefName` is not the checked-out branch, stop and tell the user to check the PR out first (`gh pr checkout <n>`); the review reads the working tree, so it can't review a branch that isn't checked out.
3. **The current branch has an open PR** — `gh pr view --json baseRefName --jq .baseRefName` (no PR, no `gh`, or no GitHub remote → fall through).
4. **Otherwise** — the default branch, `main`.

Then diff against the remote copy of that branch, so a stale local branch doesn't leak already-merged commits into the review:

```bash
BASE_BRANCH=<resolved base>
git fetch origin "$BASE_BRANCH" --quiet 2>/dev/null
if git rev-parse --verify --quiet "origin/$BASE_BRANCH" >/dev/null; then BASE="origin/$BASE_BRANCH"; else BASE="$BASE_BRANCH"; fi
git rev-parse --verify --quiet "$BASE" >/dev/null || echo "base $BASE_BRANCH not found"

git diff "$BASE"...HEAD --name-only
git diff "$BASE"...HEAD --stat
git rev-parse --abbrev-ref HEAD
```

Shell variables don't survive between Bash calls, so write the resolved ref (e.g. `origin/desktop-app`) literally into every later command that uses `$BASE`. If the base can't be found, tell the user which base you resolved and how, and stop — never fall back to `main` silently. State the base and how it was chosen (named / PR #n / default) in the report header.

If there are no changes vs the base, inform the user and stop. Note which area(s) the diff touches — backend (`src/job_applier/`, `tests/`), frontend (`web/`), desktop (`desktop/`) — so the auto-fix step (Step 4) runs the right verification commands.

**Presence signals (for conditional domain agents).** These are the domain agents in this skill's `PROJECT_CONFIGS` entry that are marked `conditional`, with the signals each one needs. The engine runs a conditional agent only when `scope.present` includes at least one of its signals, so this table and the detection lines below must cover every signal it lists.

| Conditional agent (`key`) | Runs when `scope.present` includes any of |
|---|---|
| `migrations` | `schema` |
| `sveltekit` | `web` |
| `ai-safety` | `ai` |

Derive the signals from the `--name-only` list, with one detection line per signal in the table:

```bash
git diff "$BASE"...HEAD --name-only | grep -qE '^src/job_applier/models/|^src/job_applier/maintenance\.py|^tests/test_migrations\.py' && echo schema
git diff "$BASE"...HEAD --name-only | grep -qE '^web/' && echo web
git diff "$BASE"...HEAD --name-only | grep -qE '^src/job_applier/(ai/|drafts\.py|pdf\.py)|^desktop/main\.js|^\.claude/commands/' && echo ai
```

Collect the emitted signals into `scope.present` (e.g. `["schema", "web"]`). When a changed file plausibly belongs to a signal that its pattern missed, add the signal anyway: an extra signal costs one agent run, while a missing one skips a review dimension. Baseline agents are never conditional, so they always run. An empty or omitted `scope.present` runs every agent.

### Step 2: Run the review Workflow

Call the **Workflow** tool with:
- `scriptPath`: `.claude/skills/_shared/review-engine.workflow.js`
- `args`: `{ "configKey": "code-review", "scope": { … } }` — runtime scope ONLY, with `scope.branch`, `scope.base` and `scope.present` filled from Step 1.

> **Keep `args` small — the config does NOT go in it.** A workflow script's only
> inputs are its own text and `args`; the text has no practical size limit, but a
> large `args` payload is stringified by the harness and arrives **truncated**,
> killing the run in the engine's own `JSON.parse` before any agent starts. The
> per-project config therefore lives in `PROJECT_CONFIGS` inside the engine file,
> and `args` carries only `configKey` + `scope`. Workflow scripts have no file or
> network access, so a sibling config file is not an option.

Pass exactly this as `args` — nothing else, and never a `config` key:

```json
{
  "configKey": "code-review",
  "scope": {
    "branch": "<current branch from Step 1>",
    "base": "<$BASE from Step 1, e.g. origin/desktop-app>",
    "date": "<today>",
    "summary": "branch diff vs <base branch>",
    "present": [
      "<signals from Step 1>"
    ]
  }
}
```

> The **six baseline agents** (Test Quality, DRY, Architecture, Best Practices, Error Handling, Correctness & Caller-Impact) always run. This project also carries a **Documentation** agent (docstrings / TSDoc, category `docs`) and three domain agents appended after it: **DB Migration & Schema Safety** (`migrations`), **SvelteKit Frontend Conventions** (`sveltekit`), and **AI Sandbox & Untrusted-Input Safety** (`ai-safety`, category `security`). **Domain agents are additive — they never replace a baseline agent.** To change an agent's checklist, edit `PROJECT_CONFIGS['code-review']` in the engine file.

> **Cost: keep quality, cut waste.** Every agent runs on the session default model — code review is reasoning work, so the engine does NOT downgrade agents to a cheaper tier. Cost on a diff review is controlled instead by the **conditional-agent lever**: the three domain agents run only when their signal appears in `scope.present`, so the SvelteKit agent sits out a backend-only diff, the migration agent sits out a diff that doesn't touch the schema, etc. — deterministic, fail-open, and logged. The semantic-merge agent always runs in diff mode (it carries the pre-existing-vs-new downgrade).

### Step 3: Compile & Present Results

The Workflow returns `{ mode, branch, base, findings[], counts, verdict, topConcerns }`. The engine has already deduplicated (semantic merge), applied the pre-existing-vs-new downgrade, resolved contradictions, sorted by severity then `categoryOrder`, and computed the verdict in JS. Render it:

```
## Code Review: [branch-name] vs [result.base] ([how the base was chosen: named / PR #n / default])

### Critical Issues
(Must fix before merging)
- [category] file:line — description

### Warnings
(Should fix — could cause problems)
- [category] file:line — description

### Suggestions
(Nice to have — improve code quality)
- [category] file:line — description

### Summary
- X critical, Y warnings, Z suggestions
- Overall: PASS / NEEDS WORK / BLOCK  (from result.verdict)
```

Do not re-derive the verdict — use `result.verdict` (the engine applies: any critical → BLOCK; more than `warningThreshold` (3) warnings → NEEDS WORK; otherwise PASS).

**Degraded runs:** if `result.degraded` is true (`result.failedAgents > 0`), some agents died (rate limit, timeout, API error). Tell the user the review was incomplete and name the failed dimensions (`result.failedAgentKeys`); offer to re-run, and do NOT proceed to auto-fix on a degraded run. If `result.verdict` is `INCOMPLETE …` (every agent failed), report that no analysis ran — never treat it as PASS.

### Step 4: Auto-Fix on BLOCK / NEEDS WORK

This step runs in the MAIN LOOP (here), not in the workflow — the engine returned findings only.

If the verdict is **PASS**, stop here — present the report and you're done.

If the verdict is **BLOCK** or **NEEDS WORK**, do NOT ask permission. First run `git status --porcelain` and note any files that already have uncommitted changes — those are the user's work in progress, not yours. Then immediately address every high-level item that drove the verdict:

- **BLOCK** → fix every `critical` finding.
- **NEEDS WORK** → fix every `warning` finding (criticals too, if somehow present).

**What counts as "high level":**
- All `critical` and `warning` items in the categories the review reported on (tests, DRY, architecture, Python/TypeScript best practices, error handling, correctness & caller-impact, docs, migrations, SvelteKit, AI safety).
- Do NOT auto-apply `suggestion` items — those wait for explicit user direction.

**Fix-vs-defer rubric.** Default to fixing. A finding is fixable when the correct change is determinable from the code and the review's own description — apply it. **"I'm not sure" is not a valid reason to defer:** if you're unsure how to fix a real finding, investigate (read the surrounding code, the callers, the tests) until you are sure, then fix it. Defer only for the specific reasons listed below — never out of uncertainty.

**How to address them:**
1. Group findings by file to minimize churn.
2. For each finding, make the smallest correct change that resolves the issue. Don't bundle unrelated cleanup.
3. Add or update tests when the finding is test-related (missing test, untested branch, untested failure status).
4. **Re-run the project's full verification, not just unit tests.** Run `make test` (pytest + vitest). If backend files changed, also run `make lint`; if frontend files changed, also run `cd web && npm run check`. Run Python through `uv run`. If anything fails, fix the failures before reporting done — do not stop at red.
5. **Commit the fixes in groups, then push them to the current branch.** Do this only once verification is green; if it is still red after your fixes, commit nothing and report the failures.
   - **Group related fixes into separate commits.** One commit per concern, e.g. "map the PDF renderer failure to 503", "add tests for the dropped-verdict branch", "extract the duplicated slug normalization". A fix and the tests written for it go in the same commit. Fixes that don't share a concern don't share a commit; don't split one fix across commits either.
   - **Stage by path** (`git add <file>…`), never `git add -A` / `git add .`, so nothing outside the fixes is swept in — the repo's generated artifacts (`applications/`, `data/`) are gitignored, but working-tree noise isn't. If a fix touched a file that already had uncommitted changes before Step 4, leave that file out of every commit and say so in the report — committing it would also commit the user's unfinished work.
   - **Message style:** match the project's convention (CLAUDE.md, else `git log --oneline -10`). Add any commit trailers (such as `Co-Authored-By`) that the session's attribution instructions call for.
   - **Push:** `git push` when the branch has an upstream, otherwise `git push -u origin HEAD`. Never force-push, and never use `--no-verify`. If the push is rejected (the remote moved on, or the branch belongs to a fork you can't push to), leave the commits local and report it — don't rebase or force.
   - **Never commit to `main`.** If the checked-out branch is `main`, leave the fixes uncommitted in the working tree and say so.

**When to defer an item instead of fixing it:**
- The fix requires a product/design decision the user hasn't made (e.g., "should this preference be per-profile or shared?").
- The fix would expand scope well beyond the current branch (e.g., splitting a large module that pre-dates this branch).
- The finding is on pre-existing code the branch only touched incidentally.
- The fix conflicts with an explicit choice the user defended earlier in the conversation.

Defer means: leave the code as-is and call it out in the final report. Do NOT silently skip.

### Step 5: Final Report

After fixes land (and `make test` is green), report:

```
## Auto-Fix Report

### Fixed
- file:line — what changed and why (one line each)

### Commits
- <short sha> <subject> — the findings it fixes (one line per commit)
- Pushed to origin/<branch>: yes / no (and why not)
- Left uncommitted: file — why (pre-existing user changes, red verification, or on `main`); omit if none

### Deferred
- file:line — finding, and the specific reason it was deferred (must be one of the four defer reasons — never "unsure")
  - Suggested next step: how the user should approach it

### Verification
- make test: PASS / FAIL (with failing test names if FAIL)
- make lint / npm run check (when backend / frontend changed): PASS / FAIL

### Remaining Verdict
- Re-state the verdict after fixes: PASS / NEEDS WORK / BLOCK, and what (if anything) still drives a non-PASS.
```

Keep the report tight. The user will read the commits for the details — the report is the index.
