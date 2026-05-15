---
description: Analyze the active resume and propose a search profile (roles + required/excluded tech) for review at /search.
allowed-tools: Bash, Read
---

# /suggest-roles

Read the active resume, extract the skills + seniority signals it actually
demonstrates, and POST a recommended search profile back to the API. The user
reviews and accepts (or edits) it at <http://localhost:5174/search>.

This command does **not** mutate the live filter directly — it writes a draft
on `SearchProfile.recommendations_draft`. The user clicks "Replace" or "Add to
current" to apply.

## Steps

1. **Check the API is up**: `curl -sf http://127.0.0.1:8000/api/health`. If it
   fails, tell the user to run `make api`.

2. **Fetch the active resume**:
   `curl -sS http://127.0.0.1:8000/api/resume/current`

   If you get 404, stop and tell the user to upload a resume at
   <http://localhost:5174/resume> first.

3. **Fetch the current profile** so you don't duplicate what's already there:
   `curl -sS http://127.0.0.1:8000/api/search-profile`

4. **Analyze the resume**. From `extracted_text`, identify:
   - **Concrete technical skills** the resume *demonstrates* (not just lists in
     a "skills" section with no project context). Be conservative — a one-line
     mention with no project beats nothing, but rate it lower in your rationale.
   - **Seniority signals**: years of experience, scope of roles, titles held.
     Translate to which seniority terms apply (`senior`, `staff`, `principal`,
     `lead`, `architect`, etc).
   - **Disciplines / role shapes** the resume is strongest at — generate 3-6
     plausible *role titles* the user could realistically target.
   - **Tech the user has clearly avoided** based on the resume (e.g. if every
     project is React/Vue, Angular is a fair "exclude"). Ask before assuming
     — when in doubt, leave it out; user can add their own.

5. **Generate the payload**:
   ```json
   {
     "role_titles":      ["Senior Full-Stack Engineer", "Staff Backend Engineer", ...],
     "seniority_terms":  ["senior", "staff", "principal", "lead"],
     "required_tech":    ["typescript", "react", "node", "postgres", ...],
     "excluded_tech":    ["angular"],
     "extracted_skills": ["TypeScript", "React", "Node.js", "PostgreSQL", ...],
     "rationale":        "1-3 sentence summary of what drove these picks."
   }
   ```

   - Required-tech should be lowercase, common forms (e.g. `node`, `nextjs`,
     `postgres` not `PostgreSQL`). Short tokens like `js` / `ts` are allowed
     but only mark a posting as "manual" (never "passed") on their own — that's
     handled by the filter.
   - `extracted_skills` is the longer reference list (any casing) the user sees
     in the UI; it informs but doesn't gate the filter.
   - Don't include location, work-auth, or salary preferences — those are
     handled by separate rules in the filter.

6. **POST the recommendation**:
   ```bash
   curl -sS -X POST http://127.0.0.1:8000/api/search-profile/recommendations \
     -H 'content-type: application/json' \
     -d @payload.json
   ```

7. **Report**: print the role titles + a one-line summary of required/excluded
   tech, and remind the user to review at <http://localhost:5174/search>.

## Notes

- This command never overwrites the live profile. It only writes the draft.
  The user explicitly applies it via the UI.
- If the resume is image-only (`extracted_text` empty or gibberish), tell the
  user to re-export their PDF as text-based before re-running.
- Skip preferences the user has *already* set on the active profile if your
  recommendation would just duplicate them — focus your rationale on what's new
  or different.
