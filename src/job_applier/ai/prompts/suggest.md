You are analyzing a candidate's resume to propose a job-search profile: the role
titles they could realistically target and the tech that should gate/mark
postings. This is a **recommendation only** — the user reviews and accepts it.

## Candidate resume (plain text)

{{RESUME_TEXT}}

## Current search profile (avoid duplicating what's already set)

{{CURRENT_PROFILE}}

## What to produce

From the resume, identify:

- **Concrete technical skills the resume demonstrates** (not just lists with no
  project context). Be conservative.
- **Seniority signals** (years, scope, titles) mapped to seniority terms:
  `senior`, `staff`, `principal`, `lead`, `architect`.
- **3-6 realistic role titles** the candidate could target given their strongest
  disciplines/role-shapes.
- **Tech the candidate has clearly avoided** (e.g. every project is React/Vue ->
  Angular is a fair exclude). When in doubt, leave it out.

Rules:

- `required_tech` must be lowercase common forms (`node`, `nextjs`, `postgres`,
  not `PostgreSQL`). Short tokens like `js`/`ts` are allowed.
- `extracted_skills` is the longer reference list (any casing) shown in the UI;
  it informs but doesn't gate the filter.
- **Never include location, work authorization, or salary** — those are handled
  by separate filter rules.

## Output contract — STRICT JSON ONLY

Return exactly one JSON object and nothing else (no prose, no code fences):

{"role_titles": ["Senior Full-Stack Engineer", "Staff Backend Engineer"], "seniority_terms": ["senior", "staff", "principal", "lead"], "required_tech": ["typescript", "react", "node", "postgres"], "excluded_tech": ["angular"], "extracted_skills": ["TypeScript", "React", "Node.js", "PostgreSQL"], "rationale": "1-3 sentence summary of what drove these picks."}
