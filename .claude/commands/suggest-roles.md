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

4. **Analyze the resume and build the payload** using the spec defined once in
   [`src/job_applier/ai/prompts/suggest.md`](../../src/job_applier/ai/prompts/suggest.md)
   — the same template the in-app "Suggest roles" button uses, so the two can't
   drift. Read that file and apply it verbatim: it covers which skills/seniority
   signals/role-shapes/excluded-tech to extract, the lowercase-required-tech
   rule, the never-include-location/work-auth rule, and the exact recommendation
   JSON shape. Its `{{RESUME_TEXT}}` / `{{CURRENT_PROFILE}}` placeholders map to
   the resume text and the current profile you fetched in steps 2-3.

5. **POST the recommendation**:
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
