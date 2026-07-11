"""Coverage for the user company blacklist: the service layer (normalize +
idempotent add, guard, remove), the ingest drop, and the REST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import ingest, services
from job_applier.api.app import app
from job_applier.models.db import JobPosting, get_session
from job_applier.sources.base import RawJob


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _raw(**overrides) -> RawJob:
    defaults = dict(
        source="test",
        source_id="t-1",
        url="https://example.com/jobs/1",
        title="Senior Software Engineer",
        company_name="Acme",
        description="We use TypeScript and React on Node.js.",
        location="Remote — US",
        remote=True,
    )
    defaults.update(overrides)
    return RawJob(**defaults)


class TestBlacklistService:
    def test_add_normalizes_and_stores_display_name(self, session):
        row = services.add_blacklisted_company(session, "Meta, Inc.")
        assert row.name == "Meta, Inc."
        assert row.normalized_name == "meta"

    def test_add_is_idempotent_across_naming_variants(self, session):
        a = services.add_blacklisted_company(session, "Meta, Inc.")
        b = services.add_blacklisted_company(session, "meta")
        c = services.add_blacklisted_company(session, "  META inc ")
        assert a.id == b.id == c.id
        assert len(services.list_blacklisted_companies(session)) == 1

    def test_add_keeps_optional_reason(self, session):
        row = services.add_blacklisted_company(session, "Acme", reason="  bad culture  ")
        assert row.reason == "bad culture"

    def test_blank_reason_stored_as_none(self, session):
        row = services.add_blacklisted_company(session, "Acme", reason="   ")
        assert row.reason is None

    def test_too_short_name_rejected(self, session):
        with pytest.raises(services.BlacklistNameTooShort):
            services.add_blacklisted_company(session, "!")
        with pytest.raises(services.BlacklistNameTooShort):
            services.add_blacklisted_company(session, "a")
        assert services.list_blacklisted_companies(session) == []

    def test_remove_returns_true_then_false(self, session):
        row = services.add_blacklisted_company(session, "Acme")
        assert services.remove_blacklisted_company(session, row.id) is True
        assert services.remove_blacklisted_company(session, row.id) is False
        assert services.list_blacklisted_companies(session) == []


class TestBlacklistIngest:
    def test_load_blacklisted_names_returns_normalized_set(self, session):
        services.add_blacklisted_company(session, "Meta, Inc.")
        services.add_blacklisted_company(session, "Acme")
        assert ingest.load_blacklisted_names(session) == frozenset({"meta", "acme"})

    def test_blacklisted_company_job_is_dropped(self, session):
        services.add_blacklisted_company(session, "Evil Corp")
        stats = ingest.IngestStats()
        bl = ingest.load_blacklisted_names(session)
        ingest.ingest_one(
            session, _raw(company_name="Evil Corp"), stats, blacklist=bl
        )
        assert stats.dropped_blacklist == 1
        assert stats.inserted == 0
        assert session.exec(select(JobPosting)).all() == []

    def test_naming_variant_still_matches_at_ingest(self, session):
        # "Globex" and "Globex Inc" both normalize to "globex" (the legal suffix
        # is stripped), so blacklisting one catches however a source spells it.
        services.add_blacklisted_company(session, "Globex")
        stats = ingest.IngestStats()
        bl = ingest.load_blacklisted_names(session)
        ingest.ingest_one(
            session, _raw(company_name="Globex Inc"), stats, blacklist=bl
        )
        assert stats.dropped_blacklist == 1

    def test_non_blacklisted_company_still_ingests(self, session):
        services.add_blacklisted_company(session, "Evil Corp")
        stats = ingest.IngestStats()
        bl = ingest.load_blacklisted_names(session)
        ingest.ingest_one(session, _raw(company_name="Acme"), stats, blacklist=bl)
        assert stats.dropped_blacklist == 0
        assert stats.inserted == 1
        assert stats.passed_filter == 1

    def test_no_blacklist_is_a_noop(self, session):
        stats = ingest.IngestStats()
        ingest.ingest_one(session, _raw(company_name="Evil Corp"), stats)
        assert stats.dropped_blacklist == 0
        assert stats.inserted == 1


class TestBlacklistApi:
    def test_add_list_remove_roundtrip(self, client):
        assert client.get("/api/blacklist").json() == []

        created = client.post(
            "/api/blacklist", json={"name": "Globex", "reason": "no remote"}
        )
        assert created.status_code == 200
        body = created.json()
        assert body["name"] == "Globex"
        assert body["normalized_name"] == "globex"
        assert body["reason"] == "no remote"

        listing = client.get("/api/blacklist").json()
        assert len(listing) == 1

        removed = client.request("DELETE", f"/api/blacklist/{body['id']}")
        assert removed.status_code == 204
        assert client.get("/api/blacklist").json() == []

    def test_add_is_idempotent(self, client):
        first = client.post("/api/blacklist", json={"name": "Meta, Inc."}).json()
        second = client.post("/api/blacklist", json={"name": "meta"}).json()
        assert first["id"] == second["id"]
        assert len(client.get("/api/blacklist").json()) == 1

    def test_too_short_name_is_422(self, client):
        resp = client.post("/api/blacklist", json={"name": "!"})
        assert resp.status_code == 422

    def test_delete_missing_is_404(self, client):
        assert client.request("DELETE", "/api/blacklist/999").status_code == 404
