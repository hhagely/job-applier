from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.ai import bans, drafting, providers, suggest
from job_applier.api import services
from job_applier.api.app import app
from job_applier.api.schemas import SearchProfileRecommendationIn
from job_applier.config import settings
from job_applier.models.db import (
    ApplicationStatus,
    FilterStatus,
    JobPosting,
    MatchScore,
    Resume,
    SearchProfile,
    get_session,
    set_setting,
)

SCORE_JSON = (
    '{"score": 88, "rubric": {"skills_overlap": {"points": 28, "note": "x"}, '
    '"experience_match": {"points": 24, "note": "y"}, "role_fit": {"points": 18, "note": "z"}, '
    '"domain_fit": {"points": 8, "note": "d"}, "hard_requirements": {"points": 10, "note": "h"}}, '
    '"reasoning": "Strong after tailoring."}'
)

DRAFT_ENVELOPE = (
    '{"resume_md": "# Jane Dev\\n\\n## Skills\\n**Languages:** TypeScript, Node.js\\n", '
    '"cover_letter_md": "# Jane Dev\\n\\nDear Acme team,\\n\\nI build TypeScript services.\\n\\nSincerely,\\nJane\\n"}'
)

# Envelope whose markdown is riddled with banned characters.
DIRTY_ENVELOPE = (
    '{"resume_md": "# Jane Dev\\n\\n## Summary\\nSenior \\u2014 Staff engineer with \\u201cimpact\\u201d\\u2026\\n", '
    '"cover_letter_md": "# Jane Dev\\n\\nDear Acme team,\\n\\nRange 2020\\u20132024 \\u2013 solid.\\n\\nSincerely,\\nJane\\n"}'
)


def _engine():
    e = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    return e


def _seed_resume(session, text="TypeScript, React, Node.js. 17 years senior engineer."):
    r = Resume(original_filename="r.pdf", pdf_path="/tmp/r.pdf", extracted_text=text, is_active=True)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _seed_job(session, title="Senior Engineer"):
    j = JobPosting(
        source="test",
        source_id=f"t-{title}",
        url="https://e.com/1",
        title=title,
        company_name="Acme",
        description="<p>We use <b>TypeScript</b> and Node.js.</p>",
        dedupe_hash=f"h-{title}",
        filter_status=FilterStatus.passed,
    )
    session.add(j)
    session.commit()
    session.refresh(j)
    return j


def _route_provider(draft_json: str):
    """Fake providers.run: score prompts get SCORE_JSON, draft prompts the envelope."""

    def _run(provider, prompt, **kwargs):
        if "skills_overlap" in prompt:  # the score.md rubric marker
            return SCORE_JSON
        return draft_json

    return _run


# ---- bans -----------------------------------------------------------------


def test_sanitize_replaces_all_banned_chars():
    dirty = "Senior — Staff “engineer’ role… 2020–2024 end"
    clean = bans.sanitize(dirty)
    assert clean == 'Senior - Staff "engineer\' role... 2020-2024 end'
    assert bans.find_banned(clean) == []


def test_find_banned_reports_present_chars():
    assert bans.find_banned("plain ascii") == []
    assert "—" in bans.find_banned("has — dash")


# ---- generate_draft -------------------------------------------------------


def test_generate_draft_saves_md_renders_pdf_and_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path)
    monkeypatch.setattr(providers, "run", _route_provider(DRAFT_ENVELOPE))
    monkeypatch.setattr(drafting, "_render_html_to_pdf", lambda html: b"%PDF-1.7 fake")

    from job_applier import drafts

    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        job_id = job.id
        result = drafting.generate_draft(s, "claude", job)

        assert result.tailored_score == 88
        assert result.stages == ["drafting", "rendering", "scoring", "done"]

        # Markdown + (fake) PDFs on disk.
        assert "TypeScript" in (drafts.read_markdown(job_id, "resume") or "")
        assert drafts.pdf_path(job_id, "resume").read_bytes().startswith(b"%PDF")
        assert drafts.pdf_path(job_id, "cover_letter").read_bytes().startswith(b"%PDF")

        # A tailored score row landed via the shared upsert path.
        score = s.exec(select(MatchScore).where(MatchScore.job_id == job_id)).one()
        assert score.score == 88
        assert score.score_kind == "tailored"
        assert score.scored_by == "claude-cli"
        assert score.resume_id is None  # tailored scores carry no resume id

        # Drafting marks the job "drafted".
        s.refresh(job)
        assert job.application is not None
        assert job.application.status == ApplicationStatus.drafted


def test_draft_character_bans_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path)
    monkeypatch.setattr(providers, "run", _route_provider(DIRTY_ENVELOPE))
    monkeypatch.setattr(drafting, "_render_html_to_pdf", lambda html: b"%PDF fake")

    from job_applier import drafts

    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        job_id = job.id
        result = drafting.generate_draft(s, "claude", job)
        assert result.sanitized is True

        # Persisted markdown is free of every banned character.
        for kind in ("resume", "cover_letter"):
            md = drafts.read_markdown(job_id, kind) or ""
            assert bans.find_banned(md) == [], f"{kind} still has banned chars"
        # And the substitutions actually happened.
        assert "-" in (drafts.read_markdown(job_id, "resume") or "")


def test_generate_draft_requires_active_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path)
    monkeypatch.setattr(providers, "run", _route_provider(DRAFT_ENVELOPE))
    e = _engine()
    with Session(e) as s:
        job = _seed_job(s)  # no resume
        with pytest.raises(Exception):  # NoActiveResume
            drafting.generate_draft(s, "claude", job)


def test_generate_draft_bad_json_retries_then_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path)
    calls = {"n": 0}

    def _run(provider, prompt, **kwargs):
        calls["n"] += 1
        return "not json at all"

    monkeypatch.setattr(providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        with pytest.raises(drafting.DraftingError):
            drafting.generate_draft(s, "claude", job)
    assert calls["n"] == 2  # original + one retry


# ---- suggest --------------------------------------------------------------

SUGGEST_JSON = (
    '{"role_titles": ["Senior Full-Stack Engineer", "Staff Backend Engineer"], '
    '"seniority_terms": ["senior", "staff"], "required_tech": ["typescript", "node"], '
    '"excluded_tech": ["angular"], "extracted_skills": ["TypeScript", "Node.js"], '
    '"rationale": "Strong TS/Node background."}'
)


def test_suggest_writes_draft_not_live_profile(monkeypatch):
    monkeypatch.setattr(providers, "run", lambda *a, **k: SUGGEST_JSON)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        # Pre-existing live profile the suggestion must not touch.
        live = SearchProfile(
            role_titles=["Existing Role"],
            seniority_terms=["senior"],
            required_tech=["python"],
        )
        s.add(live)
        s.commit()

        updated = suggest.suggest_roles(s, "claude")

        # Live fields untouched.
        assert updated.role_titles == ["Existing Role"]
        assert updated.required_tech == ["python"]
        # Recommendation stored as a draft.
        assert updated.recommendations_draft is not None
        assert updated.recommendations_draft["required_tech"] == ["typescript", "node"]
        assert "Staff Backend Engineer" in updated.recommendations_draft["role_titles"]


def test_save_recommendations_service_matches_endpoint():
    """Service and HTTP route both write only the draft, not the live fields."""
    e1, e2 = _engine(), _engine()
    rec = SearchProfileRecommendationIn(
        role_titles=["X"], required_tech=["ts"], rationale="r"
    )

    with Session(e1) as s:
        services.save_recommendations(s, rec)
        svc = s.exec(select(SearchProfile)).one()

    def _dep():
        with Session(e2) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    with TestClient(app) as c:
        c.post("/api/search-profile/recommendations", json=rec.model_dump())
    app.dependency_overrides.clear()
    with Session(e2) as s:
        http = s.exec(select(SearchProfile)).one()

    assert svc.recommendations_draft == http.recommendations_draft
    assert svc.role_titles == http.role_titles == []  # live fields untouched


# ---- endpoints (409 guards) -----------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path)
    e = _engine()

    def _dep():
        with Session(e) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    with TestClient(app) as c:
        yield c, e
    app.dependency_overrides.clear()


def test_draft_endpoint_requires_provider(client):
    c, e = client
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        jid = job.id
    r = c.post(f"/api/jobs/{jid}/ai/draft", json={})
    assert r.status_code == 409
    assert "provider" in r.json()["detail"].lower()


def test_draft_endpoint_requires_resume(client):
    c, e = client
    with Session(e) as s:
        set_setting(s, "ai_provider", "claude")
        job = _seed_job(s)
        jid = job.id
    r = c.post(f"/api/jobs/{jid}/ai/draft", json={})
    assert r.status_code == 409
    assert "resume" in r.json()["detail"].lower()


def test_draft_endpoint_404_for_missing_job(client):
    c, _e = client
    assert c.post("/api/jobs/9999/ai/draft", json={}).status_code == 404


def test_suggest_endpoint_requires_provider(client):
    c, e = client
    with Session(e) as s:
        _seed_resume(s)
    assert c.post("/api/ai/suggest-roles", json={}).status_code == 409
