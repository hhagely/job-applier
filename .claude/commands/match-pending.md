---
description: Score the pending-match queue (jobs that passed the hard filter but have no score yet) against the user's resume.
allowed-tools: Bash, Read
---

# /match-pending

Score every job in the pending-match queue against the user's resume, then write the
results back to the API. The user runs this in Claude Code so they can use their
subscription instead of paying for API tokens.

## Steps

1. **Check the API is up**: `curl -sf http://127.0.0.1:8000/api/health`. If it fails,
   tell the user to run `make api` in another terminal.

2. **Fetch the active resume**:
   `curl -sS http://127.0.0.1:8000/api/resume/current`

   The response has `extracted_text` (plain text from the uploaded PDF) — this is
   what you score against. If you get 404, stop and tell the user to upload a
   resume at http://localhost:5174/resume.

3. **Fetch the queue**:
   `curl -sS "http://127.0.0.1:8000/api/pending-match?limit=25&include_stale=true"`

   Each item has: `id`, `title`, `company_name`, `url`, `location`, `description`
   (the description is HTML — extract the text mentally; don't render it).

   Stale scores (those against an older resume) are re-evaluated against the
   current resume. The previous score is preserved automatically in history.

4. **Score each job** against the rubric below. The total `score` must equal the sum
   of the five buckets and lie in `[0, 100]`.

5. **POST results back** for each job:
   ```
   curl -sS -X POST http://127.0.0.1:8000/api/jobs/<id>/score \
     -H 'content-type: application/json' \
     -d '{"score": <total>, "rubric": {...}, "reasoning": "<2-3 sentences>", "scored_by": "claude-code"}'
   ```

6. **Auto-archive low scorers**: collect the ids of every job you scored **below 60**
   in this run (score `< 60`, so 60 itself survives — this includes the 0-scored
   disqualifications above). Archive them in one call:
   ```
   curl -sS -X POST http://127.0.0.1:8000/api/jobs/bulk-status \
     -H 'content-type: application/json' \
     -d '{"job_ids": [<id>, <id>, ...], "status": "archived"}'
   ```
   Skip this call entirely if no job scored below 60. Only archive jobs you actually
   scored in this run — never touch unscored postings.

7. **Report**: print a one-line summary per job (`<id>  <score>/100  <title>`),
   mark which ones were archived, and a final count (scored / archived).

## Rubric + output contract

The rubric (five buckets summing to 100), the hard rules, and the strict-JSON output
shape are defined **once** in [`src/job_applier/ai/prompts/score.md`](../../src/job_applier/ai/prompts/score.md)
so this command and the in-app "Score pending" button can't drift. Read that file and
apply it verbatim: it carries the bucket weights, the hard rules (Angular-primary,
fake-remote, below-Senior, Missouri state allow-lists), the two-to-three-sentence
reasoning guidance, and the exact JSON object shape you POST as `rubric` + `score` +
`reasoning`. The `{{RESUME_TEXT}}` / `{{TITLE}}` / `{{COMPANY}}` / `{{LOCATION}}` /
`{{DESCRIPTION}}` placeholders map to the resume text and each queue item's fields.

When invoked in Claude Code, use `scored_by: "claude-code"` in the POST body (the
in-app path uses `"<provider>-cli"`).

## Notes

- The API listens on `127.0.0.1:8000`. If you scored a job and got 200 OK, it's saved
  — no need to verify with a follow-up GET.
- If the description is unusually short or vague, prefer scoring conservatively in
  `role_fit` and call that out in `reasoning`.
- This command does **not** generate cover letters or resume tweaks — that's
  `/draft <job-id>` (separate command, comes next).
