---
description: Draft a tailored resume + cover letter (both PDFs) for one or more job postings. Usage: /draft <job-id> [<job-id> ...]
allowed-tools: Bash, Read, Write
argument-hint: <job-id> [<job-id> ...]
---

# /draft

Generate a tailored **resume** and **cover letter** for one or more job postings.
The user runs this in Claude Code so all LLM work stays inside their
subscription. The backend stores the markdown and renders both PDFs via
weasyprint — you do **not** generate PDFs directly.

Arguments are one or more job ids from the queue, separated by whitespace
(e.g. `/draft 1991 2003 2017`). If the user invoked this without any ids, stop
and ask which jobs they want drafted.

When multiple ids are given, draft each one **independently**: each job gets its
own tailored resume + cover letter, drawn fresh from the master resume against
that job's description. Do not copy the same draft across jobs. If one job
fails (404, etc.), report it and continue with the rest — don't abort the batch.

## Hard rule — do not fabricate

The tailored resume must contain **only** facts that are present in the user's
master resume (reordered, re-emphasized, rephrased, or omitted as needed). The
cover letter follows the same rule. You may:

- **Reorder** sections and bullets to lead with what the job cares about.
- **Rephrase** a bullet to use the job's vocabulary (e.g. "Node services" →
  "TypeScript microservices") **only when the underlying claim is unchanged** —
  if the resume says "Node", you can call it "Node.js / TypeScript" iff TS is
  also somewhere in the resume.
- **Drop** bullets, projects, or jobs that aren't relevant.
- **Tighten** wording.

You may **not**:

- Add a skill, tool, framework, employer, project, year, metric, certification,
  degree, or accomplishment that does not appear in the master resume.
- Inflate seniority, scope, or impact.
- Make up the user's address, phone, email, or links — only use what's in the
  resume.
- Claim experience the resume doesn't show, even in the cover letter.

If the job demands something the resume doesn't show, do **not** paper over it —
just don't mention it. The cover letter can lean on the strengths that do match.

## Steps

1. **Check the API**: `curl -sf http://127.0.0.1:8000/api/health`. If it fails,
   tell the user to run `make api`.

2. **Fetch the master resume once** (it's the same source for every job):
   ```
   curl -sS http://127.0.0.1:8000/api/resume/current
   ```
   The `extracted_text` field is the source of truth. If you get 404, stop and
   tell the user to upload one at http://localhost:5174/resume.

3. **For each job id**, run steps 4–8 below. Use a per-job tmp file path
   (`/tmp/job-applier-<id>-resume.md`, `/tmp/job-applier-<id>-cover.md`) so
   parallel/repeat runs don't stomp each other.

4. **Fetch the job**:
   ```
   curl -sS http://127.0.0.1:8000/api/jobs/<id>
   ```
   You'll get title, company.name, location, url, description (HTML — read it as
   text), and optionally score/rubric/reasoning. If you get 404, note it in the
   final report and skip to the next id.

5. **Draft the tailored resume markdown**. Use clean markdown:
   - `# Name` (one H1, the user's name from the resume)
   - A contact line under the name (email · phone · location · links — only
     what's in the resume)
   - `## Summary` — 2–3 sentences positioning the user *for this role*, drawn
     entirely from the resume's actual content.
   - `## Experience` — most-relevant roles first; bullets selected and reworded
     for the job, but every bullet must trace back to the original.
   - `## Skills` — only skills the resume already lists; you may regroup them
     to surface the ones the JD cares about first.
   - `## Education`, `## Projects`, etc. — include only sections the resume has,
     and only items in those sections.
   - Aim for 1 page of dense content (the PDF page is US Letter, ~10.5pt).

6. **Draft the cover letter markdown**. Use clean markdown:
   - One H1 for the user's name, optional contact line.
   - Date line.
   - `Dear <Company> team,` (use the actual company name; skip a contact name —
     we don't have one).
   - 3 paragraphs:
     1. The role + one honest sentence on why it fits, anchored in something the
        resume actually shows.
     2. 2–3 specific things from the resume that map to the JD's biggest asks.
        Be concrete, but every claim must come from the resume.
     3. A short close — interest in the team/product, availability, thanks.
   - Sign off with the user's name from the resume.
   - ~250–350 words. No filler.

7. **Save and render**. Use jq to build the JSON safely (markdown contains
   newlines and quotes), and POST it. Use the `Write` tool to put your drafts at
   `/tmp/job-applier-<id>-resume.md` and `/tmp/job-applier-<id>-cover.md` first.
   Then:
   ```
   jq -n \
     --rawfile r /tmp/job-applier-<id>-resume.md \
     --rawfile c /tmp/job-applier-<id>-cover.md \
     '{resume_md: $r, cover_letter_md: $c}' \
     | curl -sS -X POST http://127.0.0.1:8000/api/jobs/<id>/draft \
         -H 'content-type: application/json' \
         --data-binary @-
   ```

8. **Optionally set application status to `drafted`**:
   ```
   curl -sS -X PATCH http://127.0.0.1:8000/api/jobs/<id>/status \
     -H 'content-type: application/json' \
     -d '{"status":"drafted"}'
   ```

9. **After all jobs are processed, report once** with one block per job:
   - Job id + title + company.
   - One sentence summarizing how you tailored it (what you led with).
   - Anything in the JD the resume *doesn't* support, so the user knows what
     they'd be stretching if they leaned on it manually.
   - Download URLs for that job:
     - http://localhost:5174/jobs/<id>
     - http://127.0.0.1:8000/api/jobs/<id>/draft/resume.pdf
     - http://127.0.0.1:8000/api/jobs/<id>/draft/cover-letter.pdf

   Then a one-line tally at the bottom: `N drafted, M skipped/failed`.

## Notes

- The API saves the markdown to `applications/<id>/{resume,cover_letter}.md` and
  renders alongside as `.pdf`. The user can manually edit the markdown and click
  "Re-render PDFs from markdown" in the UI.
- Don't rewrite the master resume on disk — drafts live per-job under
  `applications/<id>/`.
- If the job already has a draft, overwrite it (the API does an in-place save).
