"""Shared prompt-template loading + single-job prompt rendering.

One module-level cache backs every prompt file (``score.md``, ``score_batch.md``,
``draft.md``, ``suggest.md``); ``render_job_prompt`` is the shared render for the
single-job score and draft prompts, which differ only in the template file. The
scoring, drafting, and suggest flows previously each carried their own template
loader (in two cache flavors) and near-identical placeholder substitution.
"""

from __future__ import annotations

from importlib import resources

from job_applier.ai import prompt_safety
from job_applier.contracts import html_to_text
from job_applier.models.db import JobPosting

_TEMPLATE_CACHE: dict[str, str] = {}


def load_template(name: str) -> str:
    """Read and cache ``prompts/<name>`` (utf-8). Shared cache across all flows."""
    cached = _TEMPLATE_CACHE.get(name)
    if cached is None:
        cached = (
            resources.files("job_applier.ai")
            .joinpath(f"prompts/{name}")
            .read_text(encoding="utf-8")
        )
        _TEMPLATE_CACHE[name] = cached
    return cached


def render_job_prompt(template_name: str, resume_text: str, job: JobPosting) -> str:
    """Render a single-job prompt (score or draft) from ``template_name``.

    The JD HTML is flattened to text, then fenced as untrusted data with a per-call
    nonce so an injected instruction in the posting can't escape its block (see
    :mod:`prompt_safety`).
    """
    nonce = prompt_safety.new_nonce()
    description = prompt_safety.clean_untrusted(html_to_text(job.description or ""), nonce)
    return (
        load_template(template_name)
        .replace("{{RESUME_TEXT}}", resume_text.strip())
        .replace("{{TITLE}}", job.title or "")
        .replace("{{COMPANY}}", job.company.name if job.company else "Unknown")
        .replace("{{LOCATION}}", job.location or "Not specified")
        .replace("{{NONCE}}", nonce)
        .replace("{{DESCRIPTION}}", description)
    )
