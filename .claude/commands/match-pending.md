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

## Rubric (sums to 100)

| Bucket             | Weight | What to look for                                                                 |
| ------------------ | ------ | -------------------------------------------------------------------------------- |
| `skills_overlap`   | 30     | Required skills/tech the resume actually demonstrates (not just mentions).       |
| `experience_match` | 25     | Years and seniority. Senior/Staff/Principal alignment with resume's career arc.  |
| `role_fit`         | 20     | Day-to-day work matches what the resume shows the user *actually does well*.     |
| `domain_fit`       | 15     | Industry/domain familiarity. Adjacent counts partially.                          |
| `hard_requirements`| 10     | Hard gates: location, work auth, degrees, certs. All-or-nothing per requirement. |

For each bucket, return both a number and a one-line note in `rubric` JSON, e.g.:
```json
{
  "skills_overlap":   {"points": 24, "note": "TS/React strong; Rust mentioned but light"},
  "experience_match": {"points": 22, "note": "Staff-level scope matches"},
  "role_fit":         {"points": 16, "note": "platform work aligns; less infra than role asks"},
  "domain_fit":       {"points":  8, "note": "fintech adjacent — payments experience"},
  "hard_requirements":{"points": 10, "note": "remote US-OK"}
}
```

## Reasoning text

Two or three sentences total — what's strong, what's a stretch, and the single most
important honest caveat. The user reads this to decide whether to spend time tailoring
an application. Don't sandbag and don't oversell.

## Hard rules — drop the score (or skip + note) when:

- The job is clearly Angular-primary despite passing the regex filter — POST score 0
  with `reasoning: "Angular-primary stack — disqualified per user's filter"`.
- The job requires on-site presence in a single location despite being tagged remote —
  same: score 0, reasoning explains.
- The role is below Senior (e.g. "Senior" in title but body says 2-4 years total) —
  score ≤ 30 and explain.
- The posting names a US-state allow-list that omits Missouri (the regex filter
  catches the obvious cases, but if you spot one that slipped through) —
  score 0, reasoning: "state allow-list excludes Missouri".

## Notes

- The API listens on `127.0.0.1:8000`. If you scored a job and got 200 OK, it's saved
  — no need to verify with a follow-up GET.
- If the description is unusually short or vague, prefer scoring conservatively in
  `role_fit` and call that out in `reasoning`.
- This command does **not** generate cover letters or resume tweaks — that's
  `/draft <job-id>` (separate command, comes next).
