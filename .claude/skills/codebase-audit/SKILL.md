---
name: codebase-audit
description: Perform a full-codebase audit of this Python (FastAPI/SQLModel) + SvelteKit app. Analyzes tests, DRY code, architecture, Python/TypeScript best practices, error handling, correctness & call-site consistency, documentation, DB migration safety, SvelteKit conventions, and AI-sandbox safety across ALL source files — not limited to a branch diff. Use when the user wants to audit the whole project, evaluate overall code quality, or review the full codebase.
---

# codebase-audit

Comprehensive audit of the entire codebase as it exists right now. Unlike `/code-review` (scoped to a branch diff), this skill evaluates ALL source files — Python under `src/job_applier/`, TypeScript/Svelte under `web/src/`, and the Electron shell under `desktop/` — to surface issues in the current state of the app.

This skill is a **thin launcher** for the shared review engine — a Claude Code Workflow at `.claude/skills/_shared/review-engine.workflow.js`. The engine fans out one agent per review dimension (deterministically — every dimension runs every time), each returns schema-enforced findings, then the engine semantically merges them and computes the verdict in JS. The verbose per-agent reports stay OUT of this conversation; only the compiled result returns. Your job here is to (1) scout the scope inline, (2) call the Workflow with the audit config, and (3) present the result.

## When to Use
- User wants to audit the full project
- User wants a quality assessment not scoped to a PR
- User merged work without running `/code-review` and wants to catch issues
- User invokes `/codebase-audit`

## Instructions

### Step 1: Scout Scope (inline, in this conversation)

Enumerate the source files so the report header is accurate. Exclude `web/node_modules/`, `web/.svelte-kit/`, build output, `data/`, `applications/`, and tests:

```bash
# Source files
find src/job_applier -name '*.py' | wc -l
find web/src \( -name '*.ts' -o -name '*.svelte' \) -not -name '*.test.ts' | wc -l
find desktop -maxdepth 1 -name '*.js' | wc -l

# Largest modules (god-object signal for the report header)
{ find src/job_applier -name '*.py'; find web/src \( -name '*.ts' -o -name '*.svelte' \) -not -name '*.test.ts'; find desktop -maxdepth 1 -name '*.js'; } \
  | xargs wc -l | sort -rn | grep -v ' total$' | head -6

# Current branch + date for the report header
git rev-parse --abbrev-ref HEAD
```

If the user asks for a narrower scope (e.g., "just audit ingest" or "just the queue page"), resolve it to repo-relative paths (e.g. `src/job_applier/ingest.py`, `web/src/routes/+page.svelte`) and pass them as `scope.focus` in Step 2. Backend tests live in a separate tree, so for each narrowed backend path also add the matching `tests/test_<module>.py` files (find them with `ls tests/test_*.py` and a grep for the module name); frontend tests are colocated (`*.test.ts`, `page.svelte.test.ts`), so add those siblings too. Also narrow the Step 1 counts and `scope.summary` to those paths so the report header is accurate. Omit `scope.focus` for a full audit.

### Step 2: Run the audit Workflow

Call the **Workflow** tool with:
- `scriptPath`: `.claude/skills/_shared/review-engine.workflow.js`
- `args`: `{ "configKey": "codebase-audit", "scope": { … } }` — runtime scope ONLY, with `scope.precomputed.largestModules`, `scope.branch`, `scope.date` and `scope.summary` filled in from Step 1, plus `scope.focus` only when the user narrowed the audit.

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
  "configKey": "codebase-audit",
  "scope": {
    "branch": "<current branch from Step 1>",
    "date": "<today>",
    "summary": "<N> .py files under src/job_applier/, <M> .ts/.svelte files under web/src/, <K> desktop/*.js",
    "focus": [
      "<repo-relative paths from Step 1 — include ONLY when the user narrowed the audit; omit this key otherwise>"
    ],
    "precomputed": {
      "largestModules": [
        {
          "file": "<path>",
          "loc": 0
        }
      ]
    }
  }
}
```

> The **six baseline agents** always run, plus a **Documentation** agent (docstrings / TSDoc, category `docs`) and three domain agents: **DB Migration & Schema Safety**, **SvelteKit Frontend Conventions**, and **AI Sandbox & Untrusted-Input Safety**. In a full audit no agent is conditional. To change an agent's checklist, edit `PROJECT_CONFIGS['codebase-audit']` in the engine file.

> **Cost: keep quality, cut waste.** Every review agent runs on the session default model (typically Opus) — these dimensions all require real reasoning (judging whether a test exercises behavior, whether an error is genuinely swallowed, whether a duplication is worth extracting), so the engine does NOT downgrade them to a cheaper tier. The engine *can* carry a per-agent `model`/`effort` (reserve it only for a genuinely mechanical presence/coverage agent if one is ever added), but the default policy is no tiering. Cost is controlled instead by levers that don't touch finding quality: (1) `mergeThreshold` skips the semantic-merge *agent* in favor of cheap JS dedup on small runs; (2) scope — pass `scope.focus` to narrow the run to a subsystem when the user asks. In a full audit, domain agents are NOT made conditional (a whole-codebase sweep runs them all); the conditional-agent lever is for `code-review`'s diff scope, where it skips a domain agent whose files aren't in the diff.

### Step 3: Compile & Present Results

The Workflow returns a structured object: `{ mode, branch, date, focus, findings[], counts, verdict, topConcerns, metrics }`. The engine has already **deduplicated** (semantic merge), **resolved contradictions**, **sorted** by severity then `categoryOrder`, and **computed the verdict** in JS — so just render it:

```
## Codebase Audit: [branch] / [date]
[Narrowed to: result.focus — omit this line when result.focus is null]

### Critical Issues
(Must fix — architecture breakage, data desync, untested core logic, sandbox/security risks, unsafe migrations)
- [category] file:line — description

### Warnings
(Should fix — maintainability, coverage gaps, convention drift)
- [category] file:line — description

### Suggestions
(Nice to have — polish, consistency)
- [category] file:line — description

### Summary
- X critical, Y warnings, Z suggestions
- Overall: HEALTHY / NEEDS WORK / UNHEALTHY  (from result.verdict)
- Top 3 areas of concern: [result.topConcerns]
- Largest modules (god-object risk): [result.metrics.largestModules]
- Documented public functions: [result.metrics.jsdocCoverage — omit if absent]
```

Do not re-derive the verdict — use `result.verdict` (the engine applies: any critical → UNHEALTHY; more than `warningThreshold` (5) warnings → NEEDS WORK; otherwise HEALTHY).

**Degraded runs:** if `result.degraded` is true (`result.failedAgents > 0`), some agents died (rate limit, timeout, API error). Tell the user the audit was incomplete and name the failed dimensions (`result.failedAgentKeys`); offer to re-run. If `result.verdict` is `INCOMPLETE …` (every agent failed), do NOT present it as a clean pass — report that no analysis ran and re-run after the cause clears.

### Step 4: Offer Remediation

After presenting the report, ask the user if they'd like:
1. A prioritized remediation plan, or
2. Help fixing specific findings, starting with criticals.

If many findings exist, recommend landing fixes in small, themed PRs rather than one mega-branch.
