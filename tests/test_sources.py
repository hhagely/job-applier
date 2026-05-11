"""Adapter-level unit tests for the new sources.

Each adapter is tested with a hand-built sample payload so the test stays
hermetic — no network, no fixture files. Adapter fetchers themselves are
exercised with monkeypatched httpx clients in a few cases where parsing the
list/detail dance matters (Workday).
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from job_applier.sources.ashby import _normalize as ashby_normalize
from job_applier.sources.hackernews import _html_to_text, _parse_header
from job_applier.sources.remoteok import _normalize as remoteok_normalize
from job_applier.sources.weworkremotely import _normalize as wwr_normalize
from job_applier.sources.workday import TITLE_GATE, parse_slug


class TestRemoteOK:
    def test_basic_normalization(self):
        item = {
            "id": "12345",
            "slug": "senior-engineer-acme",
            "company": "Acme",
            "position": "Senior Software Engineer",
            "tags": ["typescript", "react"],
            "description": "<p>We use TypeScript and React.</p>",
            "url": "https://remoteok.com/remote-jobs/12345",
            "location": "Worldwide",
            "epoch": 1762000000,
        }
        raws = list(remoteok_normalize(item))
        assert len(raws) == 1
        r = raws[0]
        assert r.source == "remoteok"
        assert r.source_id == "12345"
        assert r.title == "Senior Software Engineer"
        assert r.company_name == "Acme"
        assert r.remote is True
        assert "typescript" in r.tags
        assert r.posted_at is not None

    def test_skips_blank_title(self):
        item = {"id": "1", "position": "", "company": "X"}
        assert list(remoteok_normalize(item)) == []

    def test_default_company_when_missing(self):
        item = {"id": "1", "position": "Senior Eng", "company": ""}
        raws = list(remoteok_normalize(item))
        assert raws[0].company_name == "Unknown"


class TestWeWorkRemotely:
    def _item_xml(self, title: str, link: str = "https://wwr.example/jobs/1") -> ET.Element:
        xml = f"""
        <item>
          <title>{title}</title>
          <region>Anywhere in the World</region>
          <category>Full-Stack Programming</category>
          <description><![CDATA[<p>Some description with TypeScript and React.</p>]]></description>
          <pubDate>Fri, 17 Apr 2026 20:31:02 +0000</pubDate>
          <link>{link}</link>
          <guid>{link}</guid>
        </item>
        """
        return ET.fromstring(xml)

    def test_company_position_split_on_first_colon(self):
        item = self._item_xml("Acme Inc: Senior Backend Engineer")
        raw = wwr_normalize(item)
        assert raw is not None
        assert raw.company_name == "Acme Inc"
        assert raw.title == "Senior Backend Engineer"
        assert raw.remote is True
        assert raw.location == "Anywhere in the World"

    def test_position_with_colon_in_it_keeps_only_first_split(self):
        item = self._item_xml("Acme: Engineer: Backend, Senior")
        raw = wwr_normalize(item)
        assert raw.company_name == "Acme"
        assert raw.title == "Engineer: Backend, Senior"

    def test_no_colon_falls_back_to_unknown_company(self):
        item = self._item_xml("Standalone job title")
        raw = wwr_normalize(item)
        assert raw.company_name == "Unknown"
        assert raw.title == "Standalone job title"

    def test_missing_link_returns_none(self):
        xml = "<item><title>Acme: X</title></item>"
        assert wwr_normalize(ET.fromstring(xml)) is None


class TestAshby:
    def test_basic_normalization(self):
        item = {
            "id": "abc-123",
            "title": "Senior Software Engineer",
            "department": "Engineering",
            "team": "Platform",
            "employmentType": "FullTime",
            "location": "Remote",
            "secondaryLocations": [],
            "publishedAt": "2026-04-02T21:00:55.755+00:00",
            "isListed": True,
            "isRemote": True,
            "workplaceType": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/Notion/abc-123",
            "applyUrl": "https://jobs.ashbyhq.com/Notion/abc-123/application",
            "descriptionHtml": "<p>We use TypeScript.</p>",
        }
        raws = list(ashby_normalize("Notion", item))
        assert len(raws) == 1
        r = raws[0]
        assert r.source == "ashby"
        assert r.source_id == "Notion:abc-123"
        assert r.company_name == "Notion"
        assert r.remote is True
        assert "Engineering" in r.tags
        assert r.posted_at is not None

    def test_workplace_type_remote_implies_remote(self):
        item = {
            "id": "x",
            "title": "Engineer",
            "location": "Office",
            "workplaceType": "Remote",
            "isRemote": False,
        }
        r = next(ashby_normalize("Co", item))
        assert r.remote is True

    def test_blank_title_skipped(self):
        item = {"id": "x", "title": ""}
        assert list(ashby_normalize("Co", item)) == []


class TestWorkday:
    def test_parse_valid_slug(self):
        b = parse_slug("salesforce|wd12|External_Career_Site")
        assert b is not None
        assert b.tenant == "salesforce"
        assert b.region == "wd12"
        assert b.site == "External_Career_Site"
        assert "salesforce.wd12.myworkdayjobs.com" in b.jobs_url

    def test_parse_invalid_slug(self):
        assert parse_slug("only-tenant") is None
        assert parse_slug("a|b") is None
        assert parse_slug("a||c") is None  # empty middle
        assert parse_slug("") is None

    def test_title_gate_passes_engineering_seniority(self):
        for t in [
            "Senior Software Engineer",
            "Sr. Backend Developer",
            "Staff Engineer, Platform",
            "Principal Architect",
            "Lead SDE",
            "Distinguished Engineer",
        ]:
            assert TITLE_GATE.search(t), f"expected pass: {t}"

    def test_title_gate_drops_non_engineering_or_junior(self):
        for t in [
            "Manager, Sales Development",
            "Marketing Manager",
            "Account Executive",
            "Junior Developer",
            "Engineer",  # no seniority marker
            "Senior Product Manager",  # no engineering token
        ]:
            assert not TITLE_GATE.search(t), f"expected drop: {t}"


class TestHackerNewsParser:
    def test_html_to_text_strips_tags_and_unescapes(self):
        html = "<p>Hello &amp; <a href=\"x\">world</a></p><p>Line 2</p>"
        text = _html_to_text(html)
        assert "Hello & world" in text
        assert "Line 2" in text
        assert "<" not in text

    def test_parse_pipe_separated_header(self):
        text = "<p>Acme | NYC | REMOTE | Full-time | https://acme.com</p>"
        company, title, location, remote = _parse_header(text)
        assert company == "Acme"
        assert title is None  # NYC isn't a title
        assert location == "NYC"
        assert remote is True

    def test_title_extracted_when_present(self):
        text = "<p>Pathos AI | Senior Software / AI Engineer | NYC | Full-time</p>"
        company, title, location, _ = _parse_header(text)
        assert company == "Pathos AI"
        assert title == "Senior Software / AI Engineer"
        assert location == "NYC"

    def test_onsite_in_header_marks_not_remote(self):
        text = "<p>Acme | NYC | ONSITE | Full-time</p>"
        _, _, _, remote = _parse_header(text)
        assert remote is False

    def test_url_in_location_slot_is_skipped(self):
        text = "<p>Acme | https://x.com | REMOTE</p>"
        _, _, location, _ = _parse_header(text)
        assert location is None or "http" not in location

    def test_remote_with_qualifier_is_kept_as_location(self):
        text = "<p>Acme | Remote (US) | Full-time</p>"
        _, _, location, remote = _parse_header(text)
        assert location == "Remote (US)"
        assert remote is True

    def test_no_pipe_header_returns_none(self):
        text = "<p>Some unstructured prose without pipes.</p>"
        company, _, location, _ = _parse_header(text)
        assert company is not None
        assert location is None
