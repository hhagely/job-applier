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

## Drafting rules — single source of truth

The full drafting spec (the do-not-fabricate rule, the ASCII/character ban list,
the ATS parsing + keyword-mirroring + seniority + summary + skills-format rules,
and the cover-letter contract) lives **once** in
[`src/job_applier/ai/prompts/draft.md`](../../src/job_applier/ai/prompts/draft.md)
so this command and the in-app "Generate tailored draft" button can't drift. Read
that file and apply it verbatim. Its `{{RESUME_TEXT}}` / `{{TITLE}}` /
`{{COMPANY}}` / `{{LOCATION}}` / `{{DESCRIPTION}}` placeholders map to the master
resume text and each job's fields; produce the two markdown documents it
describes. (The backend also sanitizes the character bans server-side as a
backstop, but produce clean text.)

## Steps

1. **Check the API**: `curl -sf http://127.0.0.1:8000/api/health`. If it fails,
   tell the user to run `make api`.

2. **Fetch the master resume once** (it's the same source for every job):
   ```
   curl -sS http://127.0.0.1:8000/api/resume/current
   ```
   The `extracted_text` field is the source of truth. If you get 404, stop and
   tell the user to upload one at http://localhost:5174/resume.

3. **For each job id**, run steps 4–9 below. Use a per-job tmp file path
   (`/tmp/job-applier-<id>-resume.md`, `/tmp/job-applier-<id>-cover.md`) so
   parallel/repeat runs don't stomp each other.

4. **Fetch the job**:
   ```
   curl -sS http://127.0.0.1:8000/api/jobs/<id>
   ```
   You'll get title, company.name, location, url, description (HTML — read it as
   text), and optionally score/rubric/reasoning. If you get 404, note it in the
   final report and skip to the next id.

5. **Extract JD keywords**. Before writing anything, identify (mentally — don't
   write to disk):
   - **Hard skills** the resume genuinely supports — record the JD's *exact
     phrasing*.
   - **Practices** the resume supports (CI/CD, code review, mentorship,
     distributed systems, technical leadership, etc.) — again, exact phrasing.
   - **Seniority token** the JD targets (Senior / Staff / Lead / Principal).
   - **Domain words** repeated in the JD (e.g. "fintech", "developer tools",
     "B2B SaaS") — useful in the summary if the resume genuinely fits.

   Anything the JD asks for that the resume doesn't show: leave it out.

6. **Draft the tailored resume markdown**, following the ATS optimization rules
   above. Structure:
   - `# Name` (one H1, the user's name from the resume).
   - One contact line directly under (email, phone, location, links separated
     by `,` or ` - `; only what's in the resume; no icons).
   - `## Summary` — 2-3 sentences. First sentence states years of experience
     and mirrors the JD's seniority token when the resume supports it.
   - `## Skills` — 4-6 categorized comma-separated lines, canonical names, no
     versions. Category order leads with the JD's priorities. Every JD hard
     requirement the resume supports must appear in the JD's exact phrasing.
   - `## Experience` — most-relevant roles first. Per-role header is three
     lines: bold title, then `Company, City, ST` (or `Remote`), then dates in
     `Mon YYYY - Mon YYYY` / `Mon YYYY - Present` format. Bullets selected and
     reworded for the job, but every bullet must trace back to the original.
     The last two roles should embed several JD keywords naturally.
   - `## Education`, `## Projects`, `## Certifications` — only sections the
     resume has, only items in those sections.
   - Aim for one dense page (US Letter, ~10.5pt); two pages is acceptable at
     senior/staff level when the content is real.

7. **Draft the cover letter markdown**. Use clean markdown:
   - One H1 for the user's name, optional contact line.
   - Date line.
   - `Dear <Company> team,` (use the actual company name; skip a contact name —
     we don't have one).
   - 3 paragraphs:
     1. The role + one honest sentence on why it fits, anchored in something the
        resume actually shows.
     2. 2-3 specific things from the resume that map to the JD's biggest asks,
        using the JD's exact phrasing where the resume supports it. Every claim
        must come from the resume.
     3. A short close: interest in the team/product, availability, thanks.
   - Sign off with the user's name from the resume.
   - ~250-350 words. No filler.

8. **Save and render**. Use jq to build the JSON safely (markdown contains
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

9. **Set application status to `drafted`** (always, once the save in step 8
   succeeded — this keeps the queue's status filters honest so drafted jobs
   stop showing up as still-to-do):
   ```
   curl -sS -X PATCH http://127.0.0.1:8000/api/jobs/<id>/status \
     -H 'content-type: application/json' \
     -d '{"status":"drafted"}'
   ```
   Skip this only if step 8 failed for the job.

10. **Score the tailored draft**. Immediately after saving the draft for this
    job, invoke `/score-draft <id>` for that same id and wait for it to
    complete before moving on. The score it writes feeds into this job's line
    in the final report. If `/score-draft` errors or skips for this job
    (e.g. the save somehow produced no markdown), record `tailored: —` for
    that job and continue.

11. **After all jobs are processed, report once** with one block per job:
    - Job id + title + company.
    - One sentence summarizing how you tailored it (what you led with).
    - **Score**: one line in the form `Score: <baseline> → <tailored>`.
      - Pull `<baseline>` from `GET /api/jobs/<id>/score-history` (most-recent
        entry — that's the row snapshotted when `/score-draft` overwrote the
        active score). If history is empty (the job was never scored before
        drafting), use `—`.
      - Pull `<tailored>` from `GET /api/jobs/<id>` (the current active
        `score`). If `/score-draft` errored or skipped, use `—`.
    - **ATS keywords mirrored**: short comma-separated list of the JD's hard
      terms that made it into Skills + bullets verbatim.
    - **JD asks the resume doesn't support**: anything in the JD the resume
      doesn't show, so the user knows what they'd be stretching if they leaned
      on it manually.
    - Download URLs for that job:
      - http://localhost:5174/jobs/<id>
      - http://127.0.0.1:8000/api/jobs/<id>/draft/resume.pdf
      - http://127.0.0.1:8000/api/jobs/<id>/draft/cover-letter.pdf

    Then a one-line tally: `N drafted, M skipped/failed`.

12. **Finish with a summary table** of every job that was successfully drafted
    (skip the failed/skipped ones — they're already noted above). The job link
    points at the local SvelteKit UI on port 5174, where the user reviews and
    downloads. Markdown table, one row per drafted job:

    ```
    | Job | Company | Score | Review |
    | --- | --- | --- | --- |
    | [<title>](http://localhost:5174/jobs/<id>) | <company> | <baseline> → <tailored> | [open](http://localhost:5174/jobs/<id>) |
    ```

    - **Job** column links the title to `http://localhost:5174/jobs/<id>`.
    - **Score** is the same `<baseline> → <tailored>` values from the per-job
      block above.
    - If no jobs were drafted successfully, write `No drafts produced.` instead
      of an empty table.

## Notes

- The API saves the markdown to `applications/<id>/{resume,cover_letter}.md` and
  renders alongside as `.pdf`. The user can manually edit the markdown and click
  "Re-render PDFs from markdown" in the UI.
- Don't rewrite the master resume on disk — drafts live per-job under
  `applications/<id>/`.
- If the job already has a draft, overwrite it (the API does an in-place save).
