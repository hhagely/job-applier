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
from job_applier.sources.jibe import _normalize as jibe_normalize
from job_applier.sources.oracle import (
    _derive_company as oracle_derive_company,
)
from job_applier.sources.oracle import _combine_description as oracle_combine_description
from job_applier.sources.oracle import _normalize as oracle_normalize
from job_applier.sources.oracle import _parse_list as oracle_parse_list
from job_applier.sources.oracle import parse_slug as oracle_parse_slug
from job_applier.sources.remoteok import _normalize as remoteok_normalize
from job_applier.sources.smartrecruiters import _normalize as sr_normalize
from job_applier.sources.weworkremotely import (
    _extract_company_url as wwr_extract_url,
)
from job_applier.sources.weworkremotely import _normalize as wwr_normalize
from job_applier.sources.workable import _normalize as workable_normalize
from job_applier.sources.workday import TITLE_GATE, parse_slug
from job_applier.sources.ycombinator import (
    _extract_jobposting_ld,
    _split_hn_title,
)


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
    def _item_xml(
        self,
        title: str,
        link: str = "https://wwr.example/jobs/1",
        body_html: str = '<p>We use TypeScript. <a href="https://boards.greenhouse.io/acme/jobs/1">Apply here</a></p>',
    ) -> ET.Element:
        xml = f"""
        <item>
          <title>{title}</title>
          <region>Anywhere in the World</region>
          <category>Full-Stack Programming</category>
          <description><![CDATA[{body_html}]]></description>
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

    def test_url_points_at_extracted_company_link_not_wwr(self):
        item = self._item_xml("Acme: Senior Eng")
        raw = wwr_normalize(item)
        assert raw is not None
        assert raw.url == "https://boards.greenhouse.io/acme/jobs/1"
        assert raw.source_id == "https://wwr.example/jobs/1"  # stable WWR link
        assert raw.raw["wwr_link"] == "https://wwr.example/jobs/1"

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

    def test_description_without_external_link_is_dropped(self):
        item = self._item_xml(
            "Acme: Senior Eng",
            body_html="<p>No apply link, just prose about TypeScript.</p>",
        )
        assert wwr_normalize(item) is None

    def test_only_wwr_link_in_description_is_dropped(self):
        item = self._item_xml(
            "Acme: Senior Eng",
            body_html='<p><a href="https://weworkremotely.com/remote-jobs/foo">Apply</a></p>',
        )
        assert wwr_normalize(item) is None


class TestWWRURLExtraction:
    def test_prefers_known_ats_over_arbitrary_url(self):
        html = (
            '<p>See <a href="https://acme.com/about">about us</a> '
            'or <a href="https://jobs.lever.co/acme/abc">apply</a>.</p>'
        )
        assert wwr_extract_url(html) == "https://jobs.lever.co/acme/abc"

    def test_falls_back_to_first_external_url_when_no_ats(self):
        html = '<p>Visit <a href="https://acme.com/careers">our careers page</a>.</p>'
        assert wwr_extract_url(html) == "https://acme.com/careers"

    def test_skips_social_hosts(self):
        html = (
            '<p><a href="https://twitter.com/acme">tweet</a> '
            '<a href="https://acme.com/careers">careers</a></p>'
        )
        assert wwr_extract_url(html) == "https://acme.com/careers"

    def test_skips_wwr_self_links(self):
        html = '<p><a href="https://weworkremotely.com/jobs/123">Apply</a></p>'
        assert wwr_extract_url(html) is None

    def test_ignores_non_http_schemes(self):
        html = (
            '<p><a href="mailto:jobs@acme.com">email</a> '
            '<a href="#section">jump</a></p>'
        )
        assert wwr_extract_url(html) is None

    def test_recognises_workday_subdomain(self):
        html = '<a href="https://acme.wd5.myworkdayjobs.com/External/job/X">Apply</a>'
        assert wwr_extract_url(html) == "https://acme.wd5.myworkdayjobs.com/External/job/X"

    def test_empty_input(self):
        assert wwr_extract_url("") is None


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


_ORACLE_SLUG = (
    "eeho.fa.us2.oraclecloud.com|CX_45001"
    "|https://careers.oracle.com/en/sites/jobsearch|Oracle"
)
_ORACLE_SLUG_NOCO = (
    "eeho.fa.us2.oraclecloud.com|CX_45001"
    "|https://careers.oracle.com/en/sites/jobsearch"
)


class TestOracle:
    def test_parse_full_slug(self):
        s = oracle_parse_slug(_ORACLE_SLUG)
        assert s is not None
        assert s.api_host == "eeho.fa.us2.oraclecloud.com"
        assert s.site_number == "CX_45001"
        assert s.public_base == "https://careers.oracle.com/en/sites/jobsearch"
        assert s.company == "Oracle"
        assert "eeho.fa.us2.oraclecloud.com" in s.list_url
        assert "recruitingCEJobRequisitions" in s.list_url
        assert s.public_url("123") == (
            "https://careers.oracle.com/en/sites/jobsearch/job/123"
        )

    def test_parse_slug_derives_company_when_omitted(self):
        # A pure Fusion host has no recoverable company name (all labels are
        # noise/region/oraclecloud), so derivation falls back to the host
        # itself -- which is exactly why the seed carries an explicit company.
        s = oracle_parse_slug(_ORACLE_SLUG_NOCO)
        assert s is not None
        assert s.company == "eeho.fa.us2.oraclecloud.com"

    def test_parse_invalid_slug(self):
        assert oracle_parse_slug("host|CX_1") is None  # too few fields
        assert oracle_parse_slug("host||base") is None  # empty site number
        assert oracle_parse_slug("") is None

    def test_finder_strings_are_well_formed(self):
        s = oracle_parse_slug(_ORACLE_SLUG)
        finder = s.list_finder(limit=25, offset=50)
        assert finder.startswith("findReqs;")
        assert "siteNumber=CX_45001" in finder
        assert "limit=25" in finder
        assert "offset=50" in finder
        assert s.detail_finder("999") == 'ById;Id="999",siteNumber=CX_45001'

    def test_combine_description_keeps_html(self):
        # Oracle descriptions are rendered with {@html} on the frontend, so we
        # keep the markup and only join the sections with a blank line.
        detail = {
            "ExternalDescriptionStr": "<p>Build <b>systems</b>.</p>",
            "ExternalResponsibilitiesStr": "<ul><li>Ship.</li></ul>",
            "ExternalQualificationsStr": "",
        }
        combined = oracle_combine_description(detail)
        assert combined == "<p>Build <b>systems</b>.</p>\n\n<ul><li>Ship.</li></ul>"
        assert oracle_combine_description({}) == ""

    def test_parse_list_nested_shape(self):
        data = {
            "items": [
                {
                    "TotalJobsCount": 7,
                    "requisitionList": [{"Id": "1"}, {"Id": "2"}],
                }
            ]
        }
        postings, total = oracle_parse_list(data)
        assert [p["Id"] for p in postings] == ["1", "2"]
        assert total == 7

    def test_parse_list_empty(self):
        assert oracle_parse_list({"items": []}) == ([], None)
        assert oracle_parse_list({}) == ([], None)

    def test_normalize_builds_rawjob(self):
        s = oracle_parse_slug(_ORACLE_SLUG)
        posting = {"Id": "44", "Title": "Senior Software Engineer"}
        detail = {
            "Id": "44",
            "Title": "Senior Software Engineer",
            "ExternalDescriptionStr": "<p>Own the platform.</p>",
            "ExternalQualificationsStr": "<p>10y Python.</p>",
            "PrimaryLocation": "United States",
            "WorkplaceTypeCode": "ORA_REMOTE",
            "JobFamily": "Engineering",
        }
        raw = oracle_normalize(s, posting, detail)
        assert raw is not None
        assert raw.source == "oracle"
        assert raw.source_id == "eeho.fa.us2.oraclecloud.com:44"
        assert raw.company_name == "Oracle"
        assert raw.url == "https://careers.oracle.com/en/sites/jobsearch/job/44"
        assert "Own the platform." in raw.description
        assert "10y Python." in raw.description
        assert raw.remote is True
        assert raw.location == "United States"
        assert "Engineering" in raw.tags

    def test_normalize_remote_from_location_text(self):
        s = oracle_parse_slug(_ORACLE_SLUG_NOCO)
        raw = oracle_normalize(
            s,
            {"Id": "1", "Title": "Staff Engineer"},
            {
                "Id": "1",
                "Title": "Staff Engineer",
                "PrimaryLocation": "Remote, United States",
                "WorkplaceTypeCode": "ORA_ONSITE",
            },
        )
        assert raw is not None
        assert raw.remote is True

    def test_normalize_remote_from_bare_country_location(self):
        # A bare "United States" primary location (no city/state) is how Oracle
        # encodes a country-wide remote role, even with WorkplaceType blank.
        s = oracle_parse_slug(_ORACLE_SLUG)
        raw = oracle_normalize(
            s,
            {"Id": "1", "Title": "Principal Application Software Engineer"},
            {
                "Id": "1",
                "Title": "Principal Application Software Engineer",
                "PrimaryLocation": "United States",
                "WorkplaceType": "",
            },
        )
        assert raw is not None
        assert raw.remote is True

    def test_normalize_remote_from_bare_country_secondary_location(self):
        # A req can list specific offices and *also* carry a bare "United
        # States" secondary location -- that bare entry means it's remote.
        s = oracle_parse_slug(_ORACLE_SLUG)
        raw = oracle_normalize(
            s,
            {
                "Id": "1",
                "Title": "Lead Principal Platform Software Engineer",
                "secondaryLocations": [
                    {"Name": "Austin, TX, United States"},
                    {"Name": "United States"},
                ],
            },
            {
                "Id": "1",
                "Title": "Lead Principal Platform Software Engineer",
                "PrimaryLocation": "Nashville, TN, United States",
                "WorkplaceType": "",
            },
        )
        assert raw is not None
        assert raw.remote is True

    def test_normalize_not_remote_with_only_city_locations(self):
        # Example 3: every entry is "City, ST, United States" -- no bare country
        # entry, so it stays on-site.
        s = oracle_parse_slug(_ORACLE_SLUG)
        raw = oracle_normalize(
            s,
            {
                "Id": "1",
                "Title": "Senior Manager, Data Center Software Engineering",
                "secondaryLocations": [{"Name": "Austin, TX, United States"}],
            },
            {
                "Id": "1",
                "Title": "Senior Manager, Data Center Software Engineering",
                "PrimaryLocation": "Nashville, TN, United States",
                "WorkplaceType": "",
            },
        )
        assert raw is not None
        assert raw.remote is False

    def test_normalize_remote_from_title_marker_when_workplace_blank(self):
        # Oracle Health-style postings: WorkplaceType blank, location just
        # "United States", but the title carries an explicit remote marker.
        s = oracle_parse_slug(_ORACLE_SLUG)
        for title in (
            "Senior Application Developer - Backend Focus (Remote)",
            "[Remote] Principal Software Developer - Oracle Health",
            "Principal Software Developer - Platform Engineering- Remote",
        ):
            raw = oracle_normalize(
                s,
                {"Id": "1", "Title": title},
                {
                    "Id": "1",
                    "Title": title,
                    "PrimaryLocation": "United States",
                    "WorkplaceType": "",
                    "WorkplaceTypeCode": None,
                },
            )
            assert raw is not None
            assert raw.remote is True, title

    def test_normalize_remote_from_description_phrase(self):
        s = oracle_parse_slug(_ORACLE_SLUG)
        raw = oracle_normalize(
            s,
            {"Id": "1", "Title": "Senior Software Engineer"},
            {
                "Id": "1",
                "Title": "Senior Software Engineer",
                "ExternalDescriptionStr": "<p>This is a fully remote role.</p>",
                "PrimaryLocation": "United States",
            },
        )
        assert raw is not None
        assert raw.remote is True

    def test_normalize_not_remote_without_signal(self):
        # Blank workplace + a real city + no remote marker stays non-remote.
        s = oracle_parse_slug(_ORACLE_SLUG)
        raw = oracle_normalize(
            s,
            {"Id": "1", "Title": "Senior Software Engineer"},
            {
                "Id": "1",
                "Title": "Senior Software Engineer",
                "ExternalDescriptionStr": "<p>Build systems. Non-remote role.</p>",
                "PrimaryLocation": "Nashville, TN, United States",
                "WorkplaceType": "",
            },
        )
        assert raw is not None
        assert raw.remote is False

    def test_normalize_skips_missing_id_or_title(self):
        s = oracle_parse_slug(_ORACLE_SLUG_NOCO)
        assert oracle_normalize(s, {}, {"Title": "x"}) is None
        assert oracle_normalize(s, {}, {"Id": "1"}) is None

    def test_derive_company_skips_noise_subdomains(self):
        assert oracle_derive_company("careers.oracle.com") == "Oracle"
        assert oracle_derive_company("jobs.acme-corp.com") == "Acme Corp"


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


class TestWorkable:
    def test_basic_normalization_with_remote(self):
        item = {
            "id": 12345,
            "shortcode": "ABC123",
            "title": "Senior Software Engineer",
            "description": "<p>We build distributed systems in TypeScript.</p>",
            "requirements": "<p>5+ years TypeScript experience.</p>",
            "benefits": "<p>Unlimited PTO.</p>",
            "remote": True,
            "workplace": "remote",
            "type": "full",
            "department": ["Engineering"],
            "location": {
                "country": "United States",
                "countryCode": "US",
                "city": "Remote",
                "region": "",
            },
            "published": "2026-04-15T00:00:00.000Z",
        }
        raw = workable_normalize("rokt", item)
        assert raw is not None
        assert raw.source == "workable"
        assert raw.source_id == "rokt:ABC123"
        assert raw.title == "Senior Software Engineer"
        assert raw.company_name == "rokt"
        assert raw.remote is True
        assert "Engineering" in raw.tags
        assert "TypeScript" in raw.description
        assert raw.url == "https://apply.workable.com/rokt/j/ABC123/"
        assert raw.posted_at is not None

    def test_onsite_flag_propagates(self):
        item = {
            "id": 1,
            "shortcode": "X",
            "title": "Engineer",
            "description": "",
            "remote": False,
            "workplace": "on_site",
            "location": {"city": "NYC", "country": "United States"},
        }
        raw = workable_normalize("co", item)
        assert raw.remote is False
        assert "on_site" in raw.tags

    def test_missing_shortcode_skipped(self):
        item = {"id": 1, "title": "X"}
        assert workable_normalize("co", item) is None


class TestSmartRecruiters:
    def test_basic_normalization(self):
        item = {
            "id": "744000122509268",
            "name": "Senior Software Engineer",
            "company": {"name": "Visa", "identifier": "Visa"},
            "location": {
                "city": "Austin",
                "region": "TX",
                "country": "us",
                "fullLocation": "Austin, TX, United States",
                "remote": False,
                "hybrid": True,
            },
            "industry": {"label": "Information Technology"},
            "department": {"label": "Engineering"},
            "function": {"label": "Information Technology"},
            "typeOfEmployment": {"label": "Full-time"},
            "releasedDate": "2026-04-23T16:54:54.835Z",
            "postingUrl": "https://jobs.smartrecruiters.com/Visa/744000122509268",
            "jobAd": {
                "sections": {
                    "jobDescription": {
                        "title": "Job Description",
                        "text": "<p>Build payment systems in TypeScript.</p>",
                    },
                    "qualifications": {
                        "title": "Qualifications",
                        "text": "<p>5+ years experience.</p>",
                    },
                }
            },
        }
        raw = sr_normalize("Visa", item)
        assert raw is not None
        assert raw.source == "smartrecruiters"
        assert raw.source_id == "Visa:744000122509268"
        assert raw.company_name == "Visa"
        assert raw.title == "Senior Software Engineer"
        assert raw.location == "Austin, TX, United States"
        assert "TypeScript" in raw.description
        assert raw.employment_type == "Full-time"
        assert "hybrid" in raw.tags
        assert raw.remote is False  # hybrid, not remote

    def test_remote_flag_set_when_location_remote(self):
        item = {
            "id": "1",
            "name": "Eng",
            "company": {"name": "Co"},
            "location": {"remote": True, "fullLocation": "Remote, US"},
            "jobAd": {"sections": {}},
        }
        raw = sr_normalize("Co", item)
        assert raw.remote is True
        assert "remote" in raw.tags

    def test_missing_id_or_name_returns_none(self):
        assert sr_normalize("Co", {"name": "X"}) is None
        assert sr_normalize("Co", {"id": "1"}) is None


class TestJibe:
    def test_basic_normalization(self):
        item = {
            "slug": "5414",
            "req_id": "5414",
            "title": "Senior Software Engineer, Elasticsearch",
            "description": "<strong>About GitHub</strong><br>We build TypeScript tooling.",
            "responsibilities": "<p>Own the Elasticsearch cluster.</p>",
            "qualifications": "<p>5+ years TypeScript / Go experience.</p>",
            "location_name": "US Remote",
            "full_location": "United States",
            "location_type": "ANY",
            "country": "United States",
            "country_code": "US",
            "employment_type": "FULL_TIME",
            "posted_date": "2026-05-19T17:00:00+0000",
            "apply_url": "https://careers-githubinc.icims.com/jobs/5414/login",
            "categories": [{"name": "Engineering"}],
            "tags3": ["Engineering"],
            "tags4": ["Experienced Professional"],
            "tags5": ["Individual Contributor"],
            "hiring_organization": "GitHub, Inc.",
        }
        raw = jibe_normalize("githubinc", item)
        assert raw is not None
        assert raw.source == "jibe"
        assert raw.source_id == "githubinc:5414"
        assert raw.title == "Senior Software Engineer, Elasticsearch"
        assert raw.company_name == "GitHub, Inc."
        assert raw.remote is True
        assert "Engineering" in raw.tags
        assert "remote" in raw.tags
        assert "TypeScript" in raw.description
        assert "Elasticsearch cluster" in raw.description
        assert raw.url == "https://githubinc.jibeapply.com/jobs/5414?lang=en-us"
        assert raw.location == "US Remote"
        assert raw.employment_type == "FULL_TIME"
        assert raw.posted_at is not None

    def test_remote_inferred_from_location_name_text(self):
        item = {
            "slug": "1",
            "title": "Staff Engineer",
            "location_name": "US Remote",
            "full_location": "United States",
            "description": "",
        }
        raw = jibe_normalize("co", item)
        assert raw.remote is True
        assert "remote" in raw.tags

    def test_non_remote_location_marks_not_remote(self):
        item = {
            "slug": "2",
            "title": "Engineer",
            "location_name": "San Francisco, CA",
            "description": "",
        }
        raw = jibe_normalize("co", item)
        assert raw.remote is False
        assert "remote" not in raw.tags

    def test_falls_back_to_tenant_when_hiring_organization_missing(self):
        item = {"slug": "3", "title": "Engineer", "description": ""}
        raw = jibe_normalize("acme-corp", item)
        assert raw.company_name == "acme-corp"

    def test_missing_title_or_slug_returns_none(self):
        assert jibe_normalize("co", {"slug": "1", "title": ""}) is None
        assert jibe_normalize("co", {"title": "Engineer"}) is None

    def test_req_id_used_when_slug_missing(self):
        item = {"req_id": "REQ-999", "title": "Engineer", "description": ""}
        raw = jibe_normalize("co", item)
        assert raw.source_id == "co:REQ-999"
        assert raw.url == "https://co.jibeapply.com/jobs/REQ-999?lang=en-us"


class TestYCombinator:
    def test_split_hn_title_canonical(self):
        company, role = _split_hn_title("Kyber (YC W23) Is Hiring a Founding Marketer")
        assert company == "Kyber"
        assert role == "Founding Marketer"

    def test_split_hn_title_sr_alias(self):
        company, role = _split_hn_title(
            "Pathos AI (YC S22) Is Hiring Senior Software / AI Engineer"
        )
        assert company == "Pathos AI"
        assert role == "Senior Software / AI Engineer"

    def test_split_hn_title_unmatched(self):
        company, role = _split_hn_title("Acme is hiring engineers")
        assert company is None
        assert role is None

    def test_extract_jobposting_ld_finds_block(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@context":"https://schema.org/","@type":"JobPosting","title":"Senior Engineer","description":"<p>Build stuff with TypeScript.</p>"}
        </script>
        </head><body>...</body></html>
        """
        ld = _extract_jobposting_ld(html)
        assert ld is not None
        assert ld["title"] == "Senior Engineer"

    def test_extract_jobposting_ld_returns_none_when_missing(self):
        html = "<html><body>no JSON-LD here</body></html>"
        assert _extract_jobposting_ld(html) is None

    def test_extract_jobposting_ld_inside_graph(self):
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org/","@graph":[
            {"@type":"WebPage","name":"Page"},
            {"@type":"JobPosting","title":"Staff Engineer","description":"x"}
        ]}
        </script>
        """
        ld = _extract_jobposting_ld(html)
        assert ld is not None
        assert ld["title"] == "Staff Engineer"
