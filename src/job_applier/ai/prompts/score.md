You are scoring a single job posting against a candidate's resume. Be honest and
calibrated: this score decides whether the candidate spends time tailoring an
application. Do not sandbag and do not oversell.

## Candidate resume (plain text)

{{RESUME_TEXT}}

## Job posting

- Title: {{TITLE}}
- Company: {{COMPANY}}
- Location: {{LOCATION}}

Description:

{{DESCRIPTION}}

## Rubric (the five bucket weights sum to 100)

| Bucket              | Weight | What to look for                                                                 |
| ------------------- | ------ | -------------------------------------------------------------------------------- |
| `skills_overlap`    | 30     | Required skills/tech the resume actually demonstrates (not just mentions).       |
| `experience_match`  | 25     | Years and seniority. Senior/Staff/Principal alignment with the resume's arc.     |
| `role_fit`          | 20     | Day-to-day work matches what the resume shows the candidate actually does well.  |
| `domain_fit`        | 15     | Industry/domain familiarity. Adjacent counts partially.                          |
| `hard_requirements` | 10     | Hard gates: location, work auth, degrees, certs. All-or-nothing per requirement. |

The total `score` must equal the sum of the five bucket points and lie in `[0, 100]`.

## Hard rules — force the score down when:

- The job requires on-site presence in a single location despite being tagged remote:
  score `0`, reasoning explains.
- The role is below Senior (e.g. "Senior" in the title but the body says 2-4 years
  total): score `<= 30` and explain.
- The posting names a US-state allow-list that omits Missouri: score `0`, reasoning
  `"state allow-list excludes Missouri"`.

## Reasoning

Two or three sentences total: what is strong, what is a stretch, and the single most
important honest caveat.

## Output contract — STRICT JSON ONLY

Return exactly one JSON object and nothing else (no prose, no code fences). Each bucket
carries its integer `points` and a one-line `note`. The example fixes the shape and the
terse note style only. If the resume is tech-related, the notes may resemble the software
example shown; if it is in another field, write the notes in that field's terms — always
about **this** resume and posting.

{"score": 82, "rubric": {"skills_overlap": {"points": 26, "note": "TS/React strong; Rust light"}, "experience_match": {"points": 22, "note": "Staff-level scope matches"}, "role_fit": {"points": 16, "note": "platform work aligns"}, "domain_fit": {"points": 8, "note": "fintech adjacent"}, "hard_requirements": {"points": 10, "note": "remote US-OK"}}, "reasoning": "Strong TypeScript/React overlap at the right seniority. Infra depth is a stretch versus what the role asks. Honest caveat: domain is only adjacent."}
