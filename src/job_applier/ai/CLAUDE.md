# AI layer

Loaded when working under [src/job_applier/ai/](.). Moved out of the root `CLAUDE.md` so it isn't resident in every session.

This package is the **canonical** home for everything the LLM does.

## Layout

- [providers.py](providers.py) — the CLI registry + sandboxed runner (the single point of per-provider flag drift).
- [scoring.py](scoring.py), [drafting.py](drafting.py), [suggest.py](suggest.py) — the three flows.
- [tasks.py](tasks.py) — the in-process background-task runner the API polls for progress.
- [bans.py](bans.py) — enforces the ATS character bans **and** strips draft exfil vectors (images/links/URLs) server-side.
- [prompt_safety.py](prompt_safety.py) — fences untrusted scraped text (job descriptions, profile blobs) in per-call nonce-marked delimiters so a prompt-injected posting can't escape its block.

The prompt templates in [prompts/](prompts/) are the **source of truth** for the rubric + ATS-format rules: `score.md` (single-job scoring — baseline and tailored re-scoring share its one rubric), `score_batch.md` (the batch variant — **keep its rubric + hard rules in sync with `score.md`**; there's a SYNC comment in both), `draft.md`, `suggest.md`.

The FastAPI router is [../api/ai.py](../api/ai.py); the UI triggers are **Score pending** on `/dashboard`, **Suggest roles from resume** on `/search`, and drafting from the queue's draft cart or `/jobs/[id]`.

## Bulk scoring is batched + runs on a cheaper model tier

This avoids draining a 5-hour subscription window on a large ingest.

`score_pending` packs jobs into adaptive batches (up to `BATCH_MAX_JOBS`, capped by a JD-char budget so long JDs aren't truncated — a single over-budget JD lands in its own batch of one) and scores each batch in ONE CLI call via `score_batch.md`, so the resume + rubric prefix is sent once instead of once per job. Any job the model drops/botches (and any batch whose call fails) falls back to the single-job `score_one` path — batching never loses a job.

Baseline scoring uses `resolve_scoring_model()` (in [../api/ai.py](../api/ai.py)): the persisted `ai_scoring_model` override, else the provider's `scoring_model` default (Sonnet on Claude, Flash on Gemini), else the generation model. **Tailored re-scoring (drafting) and drafting keep the configured generation model**, not the scoring tier.

The scoring model is editable at `/settings`, as a dropdown whose choices come from the selected provider's `scoring_models` (static per provider in [providers.py](providers.py); Ollama's are read live from `ollama list` since only the user knows what they've pulled). The list is a convenience, not a whitelist — the dropdown always keeps a **Custom…** escape hatch, so a stale entry can't lock the user out of a model their CLI supports.

Every provider that names models must also pass the model through in `build_argv` (`--model` on Claude, `-m` on Gemini/Codex, a positional on `ollama run`); Codex silently dropped it until 2026-07, which made a chosen model a no-op with no error.

**A bad scoring model must never reach a bulk run**: `PUT /api/ai/provider` probes a changed (provider, model) pairing with the test prompt and 422s with the CLI's own stderr (Ollama is checked against `ollama list` instead — `ollama run <absent>` triggers a *pull*, not an error). Backstop for values that rot later: `score_pending` trips a circuit breaker after `ABORT_AFTER_PROVIDER_ERRORS` consecutive provider failures with nothing yet scored, raising `ScoringAborted` with a message naming the model and pointing at `/settings` — the failure surfaces on `/dashboard` while the control lives elsewhere, so the message *is* the repair path.
