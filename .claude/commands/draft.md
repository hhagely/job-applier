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

## Hard rule — no em dashes (and friends)

Do **not** use em dashes (`—`, U+2014) anywhere in the tailored resume or cover
letter markdown. Some ATS / recruiter screens flag em-dash-heavy text as
LLM-generated and filter it out. This applies to both documents, every section,
including the summary and cover-letter body. This rule has higher priority than
stylistic preference — if a rephrase reads slightly worse without the em dash,
ship the worse-reading version.

Use one of these instead, depending on what you meant:

- A period and a new sentence.
- A comma, semicolon, or colon.
- Parentheses for an aside.
- A hyphen (`-`) for compound modifiers (e.g. "production-ready").

The same ban extends to these "non-ASCII tells" — they're all parser-irritants
or LLM-fingerprint signals:

- **En dashes** (`–`, U+2013). Use `-` for ranges (`2020 - 2024`, not
  `2020–2024`).
- **Smart quotes** (curly `"` `"` `'` `'`). Use straight `"` and `'` only.
- **Ellipsis character** (`…`). Use three periods (`...`).
- **Non-breaking spaces, zero-width characters, decorative bullets**
  (`▪`, `▶`, `★`, etc.). The markdown bullet is plain `-`.

Before saving either markdown file, scan it for the characters above and
replace any that slipped in. (Em dashes are fine in *this* instruction file and
in your final report to the user; the rule is about the markdown you write to
`/tmp/job-applier-<id>-{resume,cover}.md`.)

## ATS optimization — what we're solving for

Most modern ATS (Workday, Greenhouse, Lever, Ashby, iCIMS) do **not** auto-reject
on resume content. They parse the PDF into a structured profile and surface
candidates to a human recruiter via keyword/boolean search and (on Workday +
iCIMS) algorithmic ranking. So the resume has to:

1. **Parse cleanly** so the structured profile is filled out correctly (title,
   employer, dates, skills extracted into the right fields).
2. **Contain the recruiter's search terms** in the JD's exact phrasing, so it
   surfaces when they run a boolean search or when Workday Skills Cloud / iCIMS
   Copilot ranks it.

Both are content/format problems, not LLM-cleverness problems. Follow the
rules below mechanically.

### Parsing — formatting rules

The markdown→PDF pipeline already gives us single-column, no-tables,
no-text-boxes, no-images output. Keep it that way:

- **No tables, no columns, no image links, no HTML.** Plain markdown only.
- **One H1** with the user's name. One contact line directly under it. No
  contact info anywhere else.
- **Only these section headings** (exact spelling): `Summary`, `Skills`,
  `Experience`, `Education`, `Projects`, `Certifications`. Don't invent
  variants like "Career Highlights" or "Tech Stack" — parsers won't recognize
  them.
- **Plain bullets** (`-` in markdown).
- **Dates**: `Mon YYYY - Mon YYYY` or `Mon YYYY - Present` (e.g.
  `Jul 2024 - Present`, `May 2020 - Feb 2024`). Three-letter month + four-digit
  year is what Workday and iCIMS parse most reliably. Don't use `07/2024`,
  `2024-07`, or season names.
- **Per-role header**: put the title on its own line so Workday's NER tags it
  as the job title cleanly:
  ```
  **Senior Software Engineer**
  Company Name, City, ST (or Remote)
  Jul 2024 - Present
  ```

### Keywords — the rule that matters most

Before drafting, **extract the hard requirements from the JD** (languages,
frameworks, tools, methodologies, seniority level). Then:

- **Every hard skill/tool/framework from the JD that the resume genuinely
  supports must appear in `## Skills`**, using the JD's exact phrasing. If the
  JD says "CI/CD pipelines", write that, not "continuous integration". If it
  says "distributed systems", don't substitute "scalable backends".
- **The same skill must also appear in at least one bullet within the last two
  roles**, embedded naturally. Recruiter search engines (Greenhouse, Lever,
  Ashby) weight in-context mentions higher than skill-list-only mentions.
- **Use canonical names** for technologies: `JavaScript` not `JS`, `TypeScript`
  not `TS`, `Node.js` not `Node`, `React` not `ReactJS`, `PostgreSQL` not
  `Postgres` (unless the JD uses the short form — then match the JD).
  Workday's Skills Cloud maps from canonical anchors.
- **No version numbers in the Skills section** (don't write `React 18`,
  `Python 3.12`). They hurt exact-match without helping recruiters.
- **No skill the master resume doesn't show.** The "do not fabricate" rule
  still wins. Better to be filtered out than to lie.

### Seniority — match the JD's token literally

Workday and iCIMS treat `Senior`, `Staff`, `Principal`, `Lead`, `Tech Lead`,
`Engineering Manager` as discrete tokens. The user's current title is **Senior
Software Engineer** — keep that exact phrasing on the most recent role. If the
JD targets "Senior", you're aligned. If it targets "Staff" or "Principal" and
the resume doesn't show that title, **do not invent it**; lean on years of
experience and scope in the summary instead.

### Summary — one specific opening line

The summary's first sentence should explicitly state years of experience
(Workday extracts this as a hard-filter input) and the seniority token that
matches the JD when the resume supports it. Example pattern:
`Senior Software Engineer with 17 years of experience building [things the JD
cares about] in remote-first environments.` Keep it 2-3 sentences total.

### Skills section format

4-6 lines, each grouping a category. Bold category label, colon, then a
comma-separated list. No bullets inside the Skills section (parsers tag
bulleted skill blocks unreliably). Example:

```
**Languages:** TypeScript, JavaScript, Python
**Frontend:** React, React Native, Redux
**Backend:** Node.js, GraphQL, REST
**Cloud and Delivery:** AWS, GCP, Docker, CI/CD
**Practices:** Full-stack development, Code review, Mentoring, Technical leadership
```

Category order should lead with the JD's priorities.

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

9. **Optionally set application status to `drafted`**:
   ```
   curl -sS -X PATCH http://127.0.0.1:8000/api/jobs/<id>/status \
     -H 'content-type: application/json' \
     -d '{"status":"drafted"}'
   ```

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

    Then a one-line tally at the bottom: `N drafted, M skipped/failed`.

## Notes

- The API saves the markdown to `applications/<id>/{resume,cover_letter}.md` and
  renders alongside as `.pdf`. The user can manually edit the markdown and click
  "Re-render PDFs from markdown" in the UI.
- Don't rewrite the master resume on disk — drafts live per-job under
  `applications/<id>/`.
- If the job already has a draft, overwrite it (the API does an in-place save).
