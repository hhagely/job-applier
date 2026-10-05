---
name: code-review
description: Perform a full code review of the currently checked out branch against its base (the open PR's base branch, else main) for this Python (FastAPI/SQLModel) + SvelteKit app. Analyzes tests, DRY code, architecture, Python/TypeScript best practices, error handling, correctness & caller-impact, documentation, DB migration safety, SvelteKit conventions, and AI-sandbox safety across the branch diff. Use when the user asks to review the branch, review a PR, or do a code review.
---

# code-review

Perform a comprehensive code review of all changes on the currently checked out branch compared to its **base**: the base branch of the branch's open PR (so a PR into the `multi-profile` integration branch, or a PR stacked on another PR, is reviewed on its own changes), or `main` when there is no open PR. Scope is limited to the branch diff — for a whole-codebase audit, use `/codebase-audit` instead.

This skill is a **thin launcher** for the shared review engine — a Claude Code Workflow at `.claude/skills/_shared/review-engine.workflow.js` (the SAME engine `/codebase-audit` uses, run with `mode: "diff"`). The engine fans out one agent per review dimension (deterministically — every dimension runs every time), each returns schema-enforced findings, then the engine semantically merges them and computes the verdict in JS. The verbose per-agent reports stay OUT of this conversation. **Auto-fix is NOT part of the workflow** — the engine returns findings only; you (the main loop) run the auto-fix in Step 4. Your job: (1) identify the diff inline, (2) call the Workflow with the review config, (3) present the result, (4) auto-fix on BLOCK/NEEDS WORK, (5) final report.

## When to Use
- User asks to review the current branch or an open PR
- User asks for a code review before merging
- User invokes `/code-review`

## Instructions

### Step 1: Identify Changes (inline, in this conversation)

Find the base first. The review agents do the same lookup, so the launcher and the agents always agree:

```bash
gh pr view --json baseRefName -q .baseRefName   # the open PR's base; if this fails or prints nothing, use main
git fetch -q origin <BASE>
```

Use `origin/<BASE>` as `<BASE_REF>` in every command below (shell variables don't persist between commands, so substitute the literal ref):

```bash
git diff <BASE_REF>...HEAD --name-only
git diff <BASE_REF>...HEAD --stat
git rev-parse --abbrev-ref HEAD
```

If there are no changes vs the base, inform the user and stop. Note which area(s) the diff touches — backend (`src/job_applier/`, `tests/`), frontend (`web/`), desktop (`desktop/`) — so the auto-fix step (Step 4) runs the right verification commands.

**Presence signals (for conditional domain agents).** These are the domain agents in this skill's `PROJECT_CONFIGS` entry that are marked `conditional`, with the signals each one needs. The engine runs a conditional agent only when `scope.present` includes at least one of its signals, so this table and the detection lines below must cover every signal it lists.

| Conditional agent (`key`) | Runs when `scope.present` includes any of |
|---|---|
| `migrations` | `schema` |
| `sveltekit` | `web` |
| `ai-safety` | `ai` |

Derive the signals from the `--name-only` list, with one detection line per signal in the table:

```bash
git diff <BASE_REF>...HEAD --name-only | grep -qE '^src/job_applier/models/|^src/job_applier/maintenance\.py|^tests/test_migrations\.py' && echo schema
git diff <BASE_REF>...HEAD --name-only | grep -qE '^web/' && echo web
git diff <BASE_REF>...HEAD --name-only | grep -qE '^src/job_applier/(ai/|drafts\.py|pdf\.py)|^desktop/main\.js|^\.claude/commands/' && echo ai
```

Collect the emitted signals into `scope.present` (e.g. `["schema", "web"]`). When a changed file plausibly belongs to a signal that its pattern missed, add the signal anyway: an extra signal costs one agent run, while a missing one skips a review dimension. Baseline agents are never conditional, so they always run. An empty or omitted `scope.present` runs every agent.

### Step 2: Run the review Workflow

Call the **Workflow** tool with:
- `scriptPath`: `.claude/skills/_shared/review-engine.workflow.js`
- `args`: `{ "configKey": "code-review", "scope": { … } }` — runtime scope ONLY, with `scope.branch` and `scope.present` filled from Step 1.

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
    "date": "<today>",
    "summary": "branch diff vs <BASE_REF>",
    "present": [
      "<signals from Step 1>"
    ]
  }
}
```

> The **six baseline agents** (Test Quality, DRY, Architecture, Best Practices, Error Handling, Correctness & Caller-Impact) always run. This project also carries a **Documentation** agent (docstrings / TSDoc, category `docs`) and three domain agents appended after it: **DB Migration & Schema Safety** (`migrations`), **SvelteKit Frontend Conventions** (`sveltekit`), and **AI Sandbox & Untrusted-Input Safety** (`ai-safety`, category `security`). **Domain agents are additive — they never replace a baseline agent.** To change an agent's checklist, edit `PROJECT_CONFIGS['code-review']` in the engine file.

> **Cost: keep quality, cut waste.** Every agent runs on the session default model — code review is reasoning work, so the engine does NOT downgrade agents to a cheaper tier. Cost on a diff review is controlled instead by the **conditional-agent lever**: the three domain agents run only when their signal appears in `scope.present`, so the SvelteKit agent sits out a backend-only diff, the migration agent sits out a diff that doesn't touch the schema, etc. — deterministic, fail-open, and logged. The semantic-merge agent always runs in diff mode (it carries the pre-existing-vs-new downgrade).

### Step 3: Compile & Present Results

The Workflow returns `{ mode, branch, findings[], counts, verdict, topConcerns }`. The engine has already deduplicated (semantic merge), applied the pre-existing-vs-new downgrade, resolved contradictions, sorted by severity then `categoryOrder`, and computed the verdict in JS. Render it:

```
## Code Review: [branch-name]

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

If the verdict is **BLOCK** or **NEEDS WORK**, do NOT ask permission. Immediately address every high-level item that drove the verdict:

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
5. Do NOT commit or push. Leave changes staged-or-unstaged in the working tree so the user can review the diff.

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

### Deferred
- file:line — finding, and the specific reason it was deferred (must be one of the four defer reasons — never "unsure")
  - Suggested next step: how the user should approach it

### Verification
- make test: PASS / FAIL (with failing test names if FAIL)
- make lint / npm run check (when backend / frontend changed): PASS / FAIL

### Remaining Verdict
- Re-state the verdict after fixes: PASS / NEEDS WORK / BLOCK, and what (if anything) still drives a non-PASS.
```

Keep the report tight. The user will read the diff for the details — the report is the index.
