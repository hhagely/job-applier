"""Tests for the configurable search profile.

Covers two surfaces:

1. The filter — ``evaluate(raw, config)`` respects a custom ``FilterConfig``,
   and ``load_active_config(session)`` reads from the DB with the right fallback
   behavior when the row is missing or has empty required lists.
2. The HTTP API — round-tripping the profile via PUT, posting recommendations
   as a draft without clobbering the active fields, and clearing the draft.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from job_applier.api.app import app
from job_applier.filters import (
    FilterConfig,
    build_config,
    evaluate,
    load_active_config,
)
from job_applier.filters.rules import _BUILTIN_DEFAULT
from job_applier.models.db import FilterStatus, SearchProfile, get_session


# ---------------------------------------------------------------------------
# Filter / FilterConfig
# ---------------------------------------------------------------------------


def test_evaluate_uses_default_config_when_none_passed(make_raw):
    # Sanity: the no-config path matches the built-in defaults, so all the
    # existing test_filter_rules.py cases keep working.
    raw = make_raw()
    assert evaluate(raw).status is FilterStatus.passed
    assert evaluate(raw, _BUILTIN_DEFAULT).status is FilterStatus.passed


def test_custom_seniority_terms_gate_the_title(make_raw):
    cfg = build_config(
        role_titles=["Principal Engineer"],
        seniority_terms=["principal"],
        required_tech=["typescript"],
        excluded_tech=[],
    )
    # "Senior ..." no longer passes once "senior" is removed from the gate.
    dropped = evaluate(make_raw(title="Senior Engineer"), cfg)
    assert dropped.status is FilterStatus.dropped
    assert "Senior" in (dropped.reason or "")

    passed = evaluate(make_raw(title="Principal Engineer"), cfg)
    assert passed.status is FilterStatus.passed


def test_required_tech_drops_when_no_match(make_raw):
    cfg = build_config(
        role_titles=[],
        seniority_terms=["senior"],
        required_tech=["rust"],
        excluded_tech=[],
    )
    # Mention TS/React but no Rust at all — the haystack genuinely lacks the
    # configured requirement.
    result = evaluate(make_raw(description="TypeScript and React shop."), cfg)
    assert result.status is FilterStatus.dropped
    assert "required-tech" in (result.reason or "") or "JavaScript" in (result.reason or "")


def test_required_tech_passes_when_listed_term_present(make_raw):
    cfg = build_config(
        role_titles=[],
        seniority_terms=["senior"],
        required_tech=["rust"],
        excluded_tech=[],
    )
    result = evaluate(make_raw(description="We write Rust services."), cfg)
    assert result.status is FilterStatus.passed


def test_short_required_tech_only_marks_manual(make_raw):
    # 2-char terms are ambiguous on their own — they should mark manual, not
    # passed.
    cfg = build_config(
        role_titles=[],
        seniority_terms=["senior"],
        required_tech=["go"],
        excluded_tech=[],
    )
    result = evaluate(
        make_raw(description="Backend role with some go work occasionally."), cfg
    )
    assert result.status is FilterStatus.manual


def test_excluded_tech_in_title_drops(make_raw):
    cfg = build_config(
        role_titles=[],
        seniority_terms=["senior"],
        required_tech=["typescript"],
        excluded_tech=["php"],
    )
    result = evaluate(make_raw(title="Senior PHP Engineer"), cfg)
    assert result.status is FilterStatus.dropped


def test_excluded_tech_in_tags_without_competing_framework_drops(make_raw):
    # The competing-framework hint list is intrinsic (react/vue/svelte/etc.);
    # PHP is not in it, so PHP-tagged with TypeScript-tagged still drops —
    # TypeScript is a language, not a competing framework.
    cfg = build_config(
        role_titles=[],
        seniority_terms=["senior"],
        required_tech=["typescript"],
        excluded_tech=["php"],
    )
    result = evaluate(
        make_raw(description="Backend role.", tags=["php", "typescript"]), cfg
    )
    assert result.status is FilterStatus.dropped


def test_empty_required_tech_list_skips_the_check(make_raw):
    # build_config compiles no regex; evaluate skips rule 7 entirely. Empty
    # required-tech is legal at the config level — only the DB-loader treats
    # it as a fallback signal (see test below).
    cfg = build_config(
        role_titles=[],
        seniority_terms=["senior"],
        required_tech=[],
        excluded_tech=[],
    )
    assert isinstance(cfg, FilterConfig)
    result = evaluate(
        make_raw(description="We are a Python and Go shop building backends."), cfg
    )
    # Would have dropped under defaults (no JS/TS); passes with no required list.
    assert result.status is FilterStatus.passed


# ---------------------------------------------------------------------------
# load_active_config (DB-backed)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_load_active_config_falls_back_to_defaults_when_no_row(db_session):
    cfg = load_active_config(db_session)
    assert cfg is _BUILTIN_DEFAULT


def test_load_active_config_falls_back_when_required_tech_empty(db_session):
    # An empty required-tech list would drop every posting; the loader treats
    # it as "unconfigured" and falls back to defaults instead.
    db_session.add(
        SearchProfile(
            role_titles=["Whatever"],
            seniority_terms=["senior"],
            required_tech=[],
            excluded_tech=[],
        )
    )
    db_session.commit()
    cfg = load_active_config(db_session)
    assert cfg is _BUILTIN_DEFAULT


def test_load_active_config_falls_back_when_seniority_empty(db_session):
    db_session.add(
        SearchProfile(
            role_titles=[],
            seniority_terms=[],
            required_tech=["rust"],
            excluded_tech=[],
        )
    )
    db_session.commit()
    cfg = load_active_config(db_session)
    assert cfg is _BUILTIN_DEFAULT


def test_load_active_config_uses_stored_lists(db_session):
    db_session.add(
        SearchProfile(
            role_titles=["Principal Platform Engineer"],
            seniority_terms=["principal"],
            required_tech=["rust", "kubernetes"],
            excluded_tech=["php"],
        )
    )
    db_session.commit()
    cfg = load_active_config(db_session)
    assert cfg is not _BUILTIN_DEFAULT
    assert cfg.seniority_terms == ["principal"]
    assert cfg.required_tech == ["rust", "kubernetes"]
    assert cfg.excluded_tech == ["php"]
    # The compiled regexes pick up the new vocabulary.
    assert cfg.seniority_re.search("Principal Platform Engineer")
    assert not cfg.seniority_re.search("Senior Engineer")


def test_default_config_has_no_home_state():
    # The built-in default must not assume any state — the rule is off until the
    # user configures one.
    assert _BUILTIN_DEFAULT.home_state is None
    assert _BUILTIN_DEFAULT.home_state_abbr is None


def test_load_active_config_carries_home_state(db_session):
    db_session.add(
        SearchProfile(
            role_titles=[],
            seniority_terms=["senior"],
            required_tech=["rust"],
            excluded_tech=[],
            home_state="Missouri",
        )
    )
    db_session.commit()
    cfg = load_active_config(db_session)
    assert cfg.home_state == "Missouri"
    assert cfg.home_state_abbr == "MO"


def test_load_active_config_keeps_home_state_when_tech_lists_empty(db_session):
    # Fresh-install shape: onboarding saves a home state before any roles/tech are
    # configured. The empty-list fallback (which uses the built-in role/tech
    # defaults) must still honor the chosen state — otherwise the very first
    # ingest silently skips the state-allow-list rule the wizard just set up.
    db_session.add(
        SearchProfile(
            role_titles=[],
            seniority_terms=[],
            required_tech=[],
            excluded_tech=[],
            home_state="Missouri",
        )
    )
    db_session.commit()
    cfg = load_active_config(db_session)
    assert cfg.home_state == "Missouri"
    assert cfg.home_state_abbr == "MO"


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session_dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_dep
    with TestClient(app) as c:
        yield c, engine
    app.dependency_overrides.clear()


def test_get_profile_returns_defaults_marker_when_empty(client):
    c, _ = client
    body = c.get("/api/search-profile").json()
    assert body["using_defaults"] is True
    assert body["id"] is None
    assert body["role_titles"] == []
    assert body["recommendations_draft"] is None


def test_put_profile_round_trips(client):
    c, _ = client
    payload = {
        "role_titles": ["Senior Full-Stack Engineer"],
        "seniority_terms": ["senior", "staff"],
        "required_tech": ["typescript", "react"],
        "excluded_tech": ["angular"],
        "extracted_skills": ["TypeScript", "React"],
    }
    put_body = c.put("/api/search-profile", json=payload).json()
    assert put_body["using_defaults"] is False
    assert put_body["role_titles"] == payload["role_titles"]
    assert put_body["required_tech"] == payload["required_tech"]
    assert put_body["id"] is not None

    # home_state defaults to None when the payload omits it.
    assert put_body["home_state"] is None

    # A subsequent PUT overwrites the same row (singleton, not append).
    second = c.put(
        "/api/search-profile",
        json={**payload, "role_titles": ["Staff Backend Engineer"]},
    ).json()
    assert second["id"] == put_body["id"]
    assert second["role_titles"] == ["Staff Backend Engineer"]


def test_put_profile_normalizes_and_clears_home_state(client):
    c, _ = client
    base = {
        "role_titles": [],
        "seniority_terms": ["senior"],
        "required_tech": ["typescript"],
        "excluded_tech": [],
        "extracted_skills": [],
    }
    # A two-letter code is normalized to the canonical full name.
    body = c.put("/api/search-profile", json={**base, "home_state": "mo"}).json()
    assert body["home_state"] == "Missouri"

    # A subsequent PUT with a blank value clears it back to None.
    cleared = c.put("/api/search-profile", json={**base, "home_state": ""}).json()
    assert cleared["home_state"] is None


def test_put_profile_rejects_unknown_home_state(client):
    c, _ = client
    resp = c.put(
        "/api/search-profile",
        json={
            "role_titles": [],
            "seniority_terms": ["senior"],
            "required_tech": ["typescript"],
            "excluded_tech": [],
            "extracted_skills": [],
            "home_state": "Ontario",
        },
    )
    assert resp.status_code == 422
    assert "Ontario" in resp.json()["detail"]


def test_post_recommendations_does_not_mutate_active_fields(client):
    c, _ = client
    c.put(
        "/api/search-profile",
        json={
            "role_titles": ["Senior Software Engineer"],
            "seniority_terms": ["senior"],
            "required_tech": ["typescript"],
            "excluded_tech": [],
            "extracted_skills": [],
        },
    )

    draft = {
        "role_titles": ["Principal Platform Engineer"],
        "seniority_terms": ["principal"],
        "required_tech": ["rust"],
        "excluded_tech": [],
        "extracted_skills": ["Rust"],
        "rationale": "Inferred from infra-heavy projects.",
    }
    body = c.post("/api/search-profile/recommendations", json=draft).json()
    assert body["role_titles"] == ["Senior Software Engineer"]
    assert body["recommendations_draft"] == draft


def test_delete_recommendations_clears_the_draft(client):
    c, _ = client
    c.post(
        "/api/search-profile/recommendations",
        json={
            "role_titles": ["X"],
            "seniority_terms": ["senior"],
            "required_tech": ["x"],
            "excluded_tech": [],
            "extracted_skills": [],
            "rationale": None,
        },
    )
    # Sanity: draft is set.
    assert c.get("/api/search-profile").json()["recommendations_draft"] is not None

    body = c.delete("/api/search-profile/recommendations").json()
    assert body["recommendations_draft"] is None


def test_delete_recommendations_is_a_noop_when_no_profile(client):
    c, _ = client
    body = c.delete("/api/search-profile/recommendations").json()
    assert body["using_defaults"] is True
    assert body["recommendations_draft"] is None
