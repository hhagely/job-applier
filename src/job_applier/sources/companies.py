"""Curated seed list of company slugs for ATS sources.

Verified live against the public boards APIs at the time of writing.
Edit freely — no validation other than "we couldn't reach the endpoint"
which is logged as a warning at ingest time.

Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
Lever:      https://api.lever.co/v0/postings/{slug}?mode=json

To find a company's Greenhouse slug, look at the URL of their careers
page if it routes through job-boards.greenhouse.io/{slug}. Same idea
for Lever (jobs.lever.co/{slug}).
"""

GREENHOUSE_COMPANIES: list[str] = [
    "discord",
    "gitlab",
    "dropbox",
    "stripe",
    "airbnb",
    "asana",
    "datadog",
    "brex",
    "chime",
    "mercury",
    "figma",
    "cloudflare",
    "vercel",
    "webflow",
    "duolingo",
]

LEVER_COMPANIES: list[str] = [
    "palantir",
]
