You are tailoring a resume and cover letter for one specific job. Produce a
tailored **resume** and **cover letter** in markdown, drawn from the candidate's
master resume against this job's description.

## Master resume (source of truth — plain text)

{{RESUME_TEXT}}

## Job posting

- Title: {{TITLE}}
- Company: {{COMPANY}}
- Location: {{LOCATION}}

Description:

{{DESCRIPTION}}

## Hard rule — do not fabricate

The tailored resume and cover letter must contain **only** facts present in the
master resume (reordered, re-emphasized, rephrased, or omitted). You MAY reorder
sections/bullets to lead with what the job cares about, rephrase a bullet to use
the job's vocabulary **only when the underlying claim is unchanged**, drop or
tighten individual bullets, and reorder freely. Keep every employer and its full
date range (see "Preserve the experience arc" below) — trim *within* a role, do
not remove whole roles. You may NOT add a skill, tool,
framework, employer, project, year, metric, certification, degree, or
accomplishment that does not appear in the master resume; inflate seniority,
scope, or impact; invent contact details; or claim experience the resume doesn't
show (including in the cover letter). If the job demands something the resume
doesn't show, do not mention it. Do not invent percentage/quantitative metrics
that aren't in the master resume.

## Hard rule — ASCII only (ATS fingerprint)

Do NOT use em dashes (—), en dashes (–), smart/curly quotes (“ ” ‘ ’), the
ellipsis character (…), non-breaking or zero-width spaces, or decorative bullets
(•, ▪, ▶, ★). Use a period + new sentence, a comma/semicolon/colon, or
parentheses instead of an em dash; a plain hyphen (-) for compound modifiers and
for ranges (2020 - 2024, not 2020–2024); straight quotes (" and ') only; three
periods (...) for an ellipsis; a plain markdown "- " for bullets. (The server
sanitizes these as a backstop, but produce clean text.)

## ATS optimization

The markdown becomes a single-column, no-tables, no-images PDF. Keep it that way:
plain markdown only (no tables, columns, HTML, or image links). Rules:

- **One H1** with the candidate's name. **One contact line** directly under it
  (email, phone, location, links from the resume only, separated by " - " or ",";
  no icons). No contact info anywhere else.
- **Only these section headings** (exact spelling): `Summary`, `Skills`,
  `Experience`, `Education`, `Projects`, `Certifications`. Only include sections
  the resume actually has. Do not invent variants like "Tech Stack".
- **Dates**: `Mon YYYY - Mon YYYY` or `Mon YYYY - Present` (e.g. `Jul 2024 -
  Present`). Three-letter month + four-digit year. Never `07/2024` or `2024-07`.
- **Per-role header is three lines**: bold title on its own line, then
  `Company, City, ST` (or `Remote`), then the date range.
- **Preserve the experience arc**: include *every* role from the master resume
  with its real title and full date range, oldest to newest, so total years and
  the seniority progression stay visible. To save space, trim or drop bullets
  *within* older roles (an older role can shrink to its header plus one line), but
  do not drop the role itself. Two pages is fine at senior/staff level; never
  sacrifice the arc to fit one page. A truncated history scores as less experience.
- **Keyword mirroring (most important)**: every hard skill/tool/framework from
  the JD that the resume genuinely supports must appear in `## Skills` using the
  JD's exact phrasing (JD says "CI/CD pipelines" -> write that), AND be
  *demonstrated in bullets*, not just listed: each such skill in at least one
  bullet, and the JD's top three or four must-haves in two or more bullets across
  different roles, embedded naturally. Scoring rewards a skill shown in real work
  context far more than a bare Skills line, so surface them in the history. Use
  canonical names (JavaScript, TypeScript, Node.js, React, PostgreSQL) unless the
  JD uses a short form. No version numbers in Skills. Never add a skill the resume
  doesn't show.
- **Seniority**: keep the resume's actual most-recent title verbatim. If the JD
  targets a higher token (Staff/Principal) the resume doesn't show, do not invent
  it; lean on years/scope in the summary instead.
- **Summary**: 2-3 sentences; the first states years of experience and mirrors
  the JD's seniority token when the resume supports it.
- **Skills section**: 4-6 lines, each a bold category label, colon, then a
  comma-separated list (no bullets inside Skills). Lead with the JD's priorities.

## Cover letter

Clean markdown: one H1 (candidate name), optional contact line, a date line,
`Dear {{COMPANY}} team,`, then three paragraphs (1: the role + one honest reason
it fits, anchored in the resume; 2: two or three specific resume facts that map
to the JD's biggest asks, using the JD's phrasing where supported; 3: a short
close). Sign off with the candidate's name. 250-350 words, no filler, every claim
from the resume.

## Output contract — STRICT JSON ONLY

Return exactly one JSON object and nothing else (no prose, no code fences), with
the two markdown documents as string values:

{"resume_md": "# Full Name\n...markdown...", "cover_letter_md": "# Full Name\n...markdown..."}
