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
- **Seniority signals** (years, scope, titles) mapped to the seniority terms that
  gate postings in the candidate's field. For a software/engineering resume those
  are typically `senior`, `staff`, `principal`, `lead`, `architect`; a resume in
  another field uses that field's ladder instead (e.g. `charge nurse`/`nurse
  manager`, or `associate`/`manager`/`director`) — take the terms from the resume.
- **3-6 realistic role titles** the candidate could target given their strongest
  disciplines/role-shapes.
- **Tech the candidate has clearly avoided** — only when the resume consistently
  uses one tool and never a common alternative to it, that alternative is a fair
  exclude. Infer this strictly from the resume; do not assume any default. When in
  doubt, leave it out (an empty list is fine and common).

Rules:

- `required_tech` is the gating keyword list, in lowercase common forms (`node`
  not `Node.js`; `cpa` not `Certified Public Accountant`). Short tokens like
  `js`/`ts` are fine. Despite the name it holds whatever a posting is matched on
  for the candidate's field — frameworks for an engineer, or the tools, methods,
  and licenses of any other profession.
- `extracted_skills` is the longer reference list (any casing) shown in the UI;
  it informs but doesn't gate the filter.
- **Never include location, work authorization, or salary** — those are handled
  by separate filter rules.

## Output contract — STRICT JSON ONLY

Return exactly one JSON object and nothing else (no prose, no code fences). The
example below fixes the shape and casing only. If the resume is tech-related, the
values may resemble the software example shown; if it is in another field, produce
that field's equivalents instead (its own role titles, keywords, and tools) —
always derive every value from **this** candidate's resume.

{"role_titles": ["Senior Full-Stack Engineer", "Staff Backend Engineer"], "seniority_terms": ["senior", "staff", "principal", "lead"], "required_tech": ["typescript", "react", "node", "postgres"], "excluded_tech": [], "extracted_skills": ["TypeScript", "React", "Node.js", "PostgreSQL"], "rationale": "1-3 sentence summary of what drove these picks."}
