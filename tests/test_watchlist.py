"""Coverage for the company whitelist: board-URL parsing, the "already being
searched" check that keeps a company from being added twice, and the add/remove
service + REST layer.

Every test stubs the network probe — the point here is the resolve/dedupe logic,
not whether a real ATS answered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import services
from job_applier.api.app import app
from job_applier.models.db import SourceSlug, get_session
from job_applier.sources import discover


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


@pytest.fixture
def probe(monkeypatch):
    """Stub the network probe. Set ``probe.boards`` to what it should 'find'."""

    class Stub:
        boards: list[discover.Board] = []
        calls: list[str] = []

        def __call__(self, name, *args, **kwargs):
            self.calls.append(name)
            return list(self.boards)

    stub = Stub()
    monkeypatch.setattr(discover, "probe_company", stub)
    return stub


@pytest.fixture
def verify(monkeypatch):
    """Stub the single-board verify used by the pasted-URL path."""

    class Stub:
        result = (True, 7, None)

        def __call__(self, board, *args, **kwargs):
            return self.result

    stub = Stub()
    monkeypatch.setattr(discover, "verify_board", stub)
    return stub


class TestParseBoardUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://boards.greenhouse.io/acme", ("greenhouse", "acme")),
            ("https://job-boards.greenhouse.io/acme/jobs/12345", ("greenhouse", "acme")),
            (
                "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                ("greenhouse", "acme"),
            ),
            (
                "https://boards.greenhouse.io/embed/job_board?for=acme",
                ("greenhouse", "acme"),
            ),
            ("https://jobs.lever.co/Acme", ("lever", "acme")),
            ("https://jobs.ashbyhq.com/acme/some-job-id", ("ashby", "acme")),
            ("https://apply.workable.com/acme/", ("workable", "acme")),
            ("https://githubinc.jibeapply.com/jobs/1?lang=en-us", ("jibe", "githubinc")),
            # No scheme — users paste bare hosts.
            ("boards.greenhouse.io/acme", ("greenhouse", "acme")),
        ],
    )
    def test_recognized_boards(self, url, expected):
        board = discover.parse_board_url(url)
        assert (board.source, board.slug) == expected

    def test_smartrecruiters_slug_keeps_case(self):
        # SmartRecruiters slugs are case-sensitive: `Visa` != `visa`.
        board = discover.parse_board_url("https://jobs.smartrecruiters.com/Visa/7439")
        assert (board.source, board.slug) == ("smartrecruiters", "Visa")

    def test_workday_url_packs_tenant_region_site(self):
        board = discover.parse_board_url(
            "https://acme.wd5.myworkdayjobs.com/en-US/External_Career_Site"
        )
        assert board.source == "workday"
        assert board.slug == "acme|wd5|External_Career_Site"

    def test_workday_cxs_api_url_also_parses(self):
        board = discover.parse_board_url(
            "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/jobs"
        )
        assert board.slug == "acme|wd1|Careers"

    @pytest.mark.parametrize(
        "text", ["Acme Corp", "", "   ", "https://example.com/careers", "greenhouse.io"]
    )
    def test_unrecognized_returns_none(self, text):
        assert discover.parse_board_url(text) is None


class TestSlugCandidates:
    def test_derives_spellings_from_a_name(self):
        assert discover.slug_candidates("Acme Corp") == ["acmecorp", "acme-corp", "acme"]

    def test_preserve_case_for_smartrecruiters(self):
        assert discover.slug_candidates("LinkedIn", preserve_case=True)[0] == "LinkedIn"

    def test_no_letters_yields_nothing(self):
        assert discover.slug_candidates("!!") == []


class TestAddByName:
    def test_adds_every_board_found(self, session, probe):
        probe.boards = [discover.Board("greenhouse", "acme", 12)]
        result = services.add_watched_company(session, "Acme")
        assert result.status == "added"
        assert [(r.source, r.slug) for r in result.companies] == [("greenhouse", "acme")]
        row = result.companies[0]
        assert row.added_by_user is True
        assert row.label == "Acme"
        assert row.last_job_count == 12
        assert row.enabled is True

    def test_added_board_shows_up_in_the_watched_list(self, session, probe):
        probe.boards = [discover.Board("lever", "acme", 3)]
        services.add_watched_company(session, "Acme")
        assert [r.slug for r in services.list_watched_companies(session)] == ["acme"]

    def test_no_board_found_raises(self, session, probe):
        probe.boards = []
        with pytest.raises(services.WatchedCompanyNotFound):
            services.add_watched_company(session, "Nowhere Ltd")
        assert services.list_watched_companies(session) == []

    def test_blank_query_raises(self, session):
        with pytest.raises(services.WatchedCompanyError):
            services.add_watched_company(session, "   ")

    def test_unreadable_url_raises_without_probing(self, session, probe):
        with pytest.raises(services.WatchedCompanyUnknownUrl):
            services.add_watched_company(session, "https://example.com/careers")
        assert probe.calls == []

    def test_blacklisted_company_is_refused(self, session, probe):
        services.add_blacklisted_company(session, "Evil Corp")
        with pytest.raises(services.WatchedCompanyBlacklisted):
            services.add_watched_company(session, "Evil Corp")
        assert probe.calls == []


class TestAlreadySearched:
    """The user-visible requirement: a company already in the search list is
    reported back, not added a second time."""

    def test_company_added_twice_is_reported_not_duplicated(self, session, probe):
        probe.boards = [discover.Board("greenhouse", "acme", 12)]
        services.add_watched_company(session, "Acme")

        again = services.add_watched_company(session, "Acme")
        assert again.status == "already_searched"
        assert "already in your search list" in again.message
        assert "greenhouse / acme" in again.message
        assert len(session.exec(select(SourceSlug)).all()) == 1

    def test_matches_a_board_that_came_from_seed_discovery(self, session, probe):
        # The common case: the company is already watched because the feed found
        # it, so `added_by_user` is False and it isn't in the user's own list.
        session.add(SourceSlug(source="greenhouse", slug="acme"))
        session.commit()

        result = services.add_watched_company(session, "Acme")
        assert result.status == "already_searched"
        assert probe.calls == []
        assert services.list_watched_companies(session) == []

    def test_matches_across_naming_variants(self, session, probe):
        session.add(SourceSlug(source="lever", slug="acme-corp"))
        session.commit()
        for spelling in ("Acme Corp", "acme corp", "ACME-CORP", "Acme, Corp."):
            assert services.add_watched_company(session, spelling).status == (
                "already_searched"
            )
        assert probe.calls == []

    def test_matches_a_workday_tenant_inside_a_packed_slug(self, session, probe):
        session.add(SourceSlug(source="workday", slug="acme|wd5|External_Career_Site"))
        session.commit()
        assert services.add_watched_company(session, "Acme").status == "already_searched"

    def test_pasted_url_for_a_watched_board_is_reported(self, session, verify):
        session.add(SourceSlug(source="greenhouse", slug="acme"))
        session.commit()
        result = services.add_watched_company(session, "https://boards.greenhouse.io/acme")
        assert result.status == "already_searched"
        assert len(session.exec(select(SourceSlug)).all()) == 1

    def test_pasted_url_for_a_company_watched_on_another_ats_is_reported(
        self, session, verify
    ):
        # Same employer, different ATS: still "already being searched", so the
        # answer doesn't depend on which of the two inputs the user reached for.
        session.add(SourceSlug(source="greenhouse", slug="acme"))
        session.commit()
        result = services.add_watched_company(session, "https://jobs.lever.co/acme")
        assert result.status == "already_searched"
        assert result.companies[0].source == "greenhouse"
        assert len(session.exec(select(SourceSlug)).all()) == 1

    def test_different_company_still_adds(self, session, probe):
        session.add(SourceSlug(source="greenhouse", slug="acme"))
        session.commit()
        probe.boards = [discover.Board("greenhouse", "globex", 4)]
        assert services.add_watched_company(session, "Globex").status == "added"


class TestAddByUrl:
    def test_verified_board_is_stored(self, session, verify):
        verify.result = (True, 5, None)
        result = services.add_watched_company(session, "https://jobs.lever.co/Acme")
        assert result.status == "added"
        row = result.companies[0]
        assert (row.source, row.slug, row.last_job_count) == ("lever", "acme", 5)
        assert row.added_by_user is True

    def test_dead_board_is_not_stored(self, session, verify):
        verify.result = (False, None, "didn't respond (HTTP 404)")
        with pytest.raises(services.WatchedCompanyUnreachable) as exc:
            services.add_watched_company(session, "https://jobs.lever.co/nope")
        assert "HTTP 404" in str(exc.value)
        assert session.exec(select(SourceSlug)).all() == []

    def test_board_with_no_openings_is_stored_when_the_source_404s_properly(
        self, session, verify
    ):
        # Ashby 404s an unknown board, so a 200 with zero openings is a real
        # employer who just isn't hiring today — exactly what you want watched.
        verify.result = (True, 0, None)
        result = services.add_watched_company(session, "https://jobs.ashbyhq.com/Clerk")
        assert result.status == "added"
        assert result.companies[0].slug == "Clerk"


class TestRemove:
    def test_remove_hand_added_row(self, session, probe):
        probe.boards = [discover.Board("greenhouse", "acme", 1)]
        row = services.add_watched_company(session, "Acme").companies[0]
        assert services.remove_watched_company(session, row.id) is True
        assert services.remove_watched_company(session, row.id) is False
        assert session.exec(select(SourceSlug)).all() == []

    def test_will_not_remove_a_discovered_row(self, session):
        # Seed/feed rows are managed by refresh-slugs; the whitelist UI must not
        # be a back door for deleting them.
        row = SourceSlug(source="greenhouse", slug="acme")
        session.add(row)
        session.commit()
        assert services.remove_watched_company(session, row.id) is False
        assert len(session.exec(select(SourceSlug)).all()) == 1


class TestWatchlistApi:
    def test_add_list_remove_roundtrip(self, client, probe):
        assert client.get("/api/watched-companies").json() == []

        probe.boards = [discover.Board("greenhouse", "acme", 9)]
        created = client.post("/api/watched-companies", json={"query": "Acme"})
        assert created.status_code == 200
        body = created.json()
        assert body["status"] == "added"
        assert body["companies"][0]["label"] == "Acme"
        assert body["companies"][0]["last_job_count"] == 9

        listing = client.get("/api/watched-companies").json()
        assert len(listing) == 1

        removed = client.request(
            "DELETE", f"/api/watched-companies/{listing[0]['id']}"
        )
        assert removed.status_code == 204
        assert client.get("/api/watched-companies").json() == []

    def test_already_searched_is_a_200_notice(self, client, probe):
        probe.boards = [discover.Board("greenhouse", "acme", 9)]
        client.post("/api/watched-companies", json={"query": "Acme"})
        again = client.post("/api/watched-companies", json={"query": "acme"})
        assert again.status_code == 200
        assert again.json()["status"] == "already_searched"
        assert len(client.get("/api/watched-companies").json()) == 1

    def test_unresolvable_company_is_422(self, client, probe):
        probe.boards = []
        resp = client.post("/api/watched-companies", json={"query": "Nowhere"})
        assert resp.status_code == 422

    def test_delete_missing_is_404(self, client):
        assert client.request("DELETE", "/api/watched-companies/999").status_code == 404
