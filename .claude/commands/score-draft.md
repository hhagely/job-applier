---
description: Re-score a tailored draft (resume markdown produced by /draft) against a job's JD using the same rubric as /match-pending. Usage: /score-draft <job-id> [<job-id> ...]
allowed-tools: Bash, Read
argument-hint: <job-id> [<job-id> ...]
---

# /score-draft

Score the **tailored** resume markdown for one or more jobs against each job's JD,
using the **same rubric and weights** as `/match-pending`. Writes a score with
`score_kind: "tailored"` so the detail page can show a `baseline → tailored`
delta.

The user runs this in Claude Code so LLM work stays inside their subscription.

Arguments are one or more job ids, separated by whitespace
(e.g. `/score-draft 41 42 43`). If invoked with no ids, stop and ask which jobs.

When multiple ids are given, score each one **independently**: one failure
(404, missing draft) does not block the rest. Report results per-id.

## Steps

1. **Check the API is up**: `curl -sf http://127.0.0.1:8000/api/health`. If it
   fails, tell the user to run `make api`.

2. **For each job id**, run steps 3–6 below.

3. **Fetch the tailored draft markdown**:
   ```
   curl -sS "http://127.0.0.1:8000/api/jobs/<id>/draft?include_markdown=true"
   ```
   - On 404 (job not found), report it and skip to the next id.
   - If the response is 200 but `resume_md` is null/empty, report
     `no tailored draft yet — run /draft first` and skip. **Do not write a
     score.**

4. **Fetch the JD**:
   ```
   curl -sS http://127.0.0.1:8000/api/jobs/<id>
   ```
   Use `title`, `company.name`, `location`, and `description` (HTML — read it
   as text).

5. **Score the tailored resume markdown** against the JD using the rubric below.
   The total `score` must equal the sum of the five buckets and lie in `[0, 100]`.

6. **POST the result back** with `score_kind: "tailored"`:
   ```
   curl -sS -X POST http://127.0.0.1:8000/api/jobs/<id>/score \
     -H 'content-type: application/json' \
     -d '{"score": <total>, "rubric": {...}, "reasoning": "<2-3 sentences>", "scored_by": "claude-code", "score_kind": "tailored"}'
   ```

7. **Report**: one line per job (`<id>  <score>/100  <title>`), plus a final
   `N scored, M skipped` tally. When invoked from `/draft`, the parent flow
   reads back `GET /api/jobs/<id>/score-history` to build the `baseline →
   tailored` delta — you don't need to print the delta here.

## Rubric (sums to 100) — identical to /match-pending

| Bucket             | Weight | What to look for                                                                 |
| ------------------ | ------ | -------------------------------------------------------------------------------- |
| `skills_overlap`   | 30     | Required skills/tech the resume actually demonstrates (not just mentions).       |
| `experience_match` | 25     | Years and seniority. Senior/Staff/Principal alignment with resume's career arc.  |
| `role_fit`         | 20     | Day-to-day work matches what the resume shows the user *actually does well*.    |
| `domain_fit`       | 15     | Industry/domain familiarity. Adjacent counts partially.                          |
| `hard_requirements`| 10     | Hard gates: location, work auth, degrees, certs. All-or-nothing per requirement. |

For each bucket, return both a number and a one-line note in `rubric` JSON,
e.g.:
```json
{
  "skills_overlap":   {"points": 26, "note": "TS/React strong; CI/CD mirrored from JD"},
  "experience_match": {"points": 22, "note": "Staff-level scope matches"},
  "role_fit":         {"points": 17, "note": "platform work aligns post-tailoring"},
  "domain_fit":       {"points":  9, "note": "fintech adjacent — payments experience"},
  "hard_requirements":{"points": 10, "note": "remote US-OK"}
}
```

## Reasoning text

Two or three sentences. Focus on what the tailoring actually changed vs. the
baseline: which JD asks the tailored resume now mirrors verbatim, which still
aren't supported, and the single most honest caveat. Don't sandbag and don't
oversell.

## Hard rules — drop the score (or skip + note) when:

- The tailored resume is somehow Angular-primary despite the user's filter —
  POST score 0 with reasoning explaining why.
- The job requires on-site presence in a single location despite being tagged
  remote — score 0, reasoning explains.
- The role is below Senior (e.g. "Senior" in title but body says 2-4 years
  total) — score ≤ 30 and explain.
- The posting names a US-state allow-list that omits Missouri — score 0,
  reasoning: "state allow-list excludes Missouri".

## Notes

- This command **only** writes when there is a tailored draft on disk. A 404
  on the draft endpoint, or a 200 with null `resume_md`, both skip cleanly.
- The API listens on `127.0.0.1:8000`. If you POSTed and got 200 OK, the score
  is saved — no follow-up GET needed.
- Stay in sync with [match-pending.md](match-pending.md). The two rubrics are
  duplicated on purpose so each command is self-contained — but the weights
  and bucket definitions must match exactly, or the `baseline → tailored`
  delta is meaningless.
