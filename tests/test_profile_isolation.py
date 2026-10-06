"""Two profiles (two people) on one install never see each other's state.

Status, notes, scores, the blacklist, preferences, and drafts are per profile.
The postings themselves are shared. Each test acts as profile A, switches to B
the way the UI does, and checks B starts clean and A's data survives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import pdf
from job_applier.ai import tasks
from job_applier.api.app import app
from job_applier.config import settings
from job_applier.models import Application, JobPosting, JobProfileLink, MatchScore
from job_applier.models.db import AppSetting, FilterStatus, get_session
from job_applier.models.scoping import current_profile_id, session_profile_id


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path / "applications")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _session_dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_dep
    with TestClient(app) as c:
        a = c.get("/api/search-profile").json()
        a_id = c.put("/api/search-profile", json={}).json()["id"]  # creates Default
        b_id = c.post("/api/search-profiles", json={"name": "Partner"}).json()["id"]
        with Session(engine) as s:
            job = JobPosting(
                source="test",
                source_id="j-1",
                url="https://example.com/1",
                title="Senior Engineer",
                description="TypeScript.",
                dedupe_hash="h-1",
                filter_status=FilterStatus.passed,
            )
            s.add(job)
            s.flush()
            # Both profiles' rules passed it: it's in both queues.
            s.add(JobProfileLink(job_id=job.id, profile_id=a_id))
            s.add(JobProfileLink(job_id=job.id, profile_id=b_id))
            s.commit()
            job_id = job.id
        assert a["using_defaults"] is True
        yield c, engine, a_id, b_id, job_id
    app.dependency_overrides.clear()


def _switch(c, profile_id):
    assert c.post(f"/api/search-profiles/{profile_id}/activate").status_code == 200


def test_application_status_and_notes_are_per_profile(setup):
    c, engine, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    c.post(f"/api/jobs/{job}/notes", json={"notes": "A's notes"})

    _switch(c, b)
    assert c.get(f"/api/jobs/{job}").json()["application"] is None
    c.patch(f"/api/jobs/{job}/status", json={"status": "interested"})

    _switch(c, a)
    app_a = c.get(f"/api/jobs/{job}").json()["application"]
    assert app_a["status"] == "applied"
    assert app_a["notes"] == "A's notes"

    with Session(engine) as s:
        rows = s.exec(select(Application).execution_options(all_profiles=True)).all()
    assert sorted((r.profile_id, r.status.value) for r in rows) == [
        (a, "applied"),
        (b, "interested"),
    ]


def test_scores_are_per_profile(setup):
    c, engine, a, b, job = setup
    c.post(f"/api/jobs/{job}/score", json={"score": 91, "rubric": {}})
    _switch(c, b)
    assert c.get(f"/api/jobs/{job}").json()["score"] is None
    c.post(f"/api/jobs/{job}/score", json={"score": 40, "rubric": {}})
    assert c.get(f"/api/jobs/{job}").json()["score"]["score"] == 40

    _switch(c, a)
    assert c.get(f"/api/jobs/{job}").json()["score"]["score"] == 91
    # B's score went into its own row, not over A's (no history snapshot of 91).
    assert c.get(f"/api/jobs/{job}/score-history").json() == []
    with Session(engine) as s:
        n = len(s.exec(select(MatchScore).execution_options(all_profiles=True)).all())
    assert n == 2


def test_status_counts_only_count_the_active_profile(setup):
    c, _, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    assert c.get("/api/jobs/status-counts").json()["counts"]["applied"] == 1
    _switch(c, b)
    counts = c.get("/api/jobs/status-counts").json()["counts"]
    assert counts["applied"] == 0
    assert counts["none"] == 1


def test_blacklist_is_per_profile(setup):
    c, _, a, b, _job = setup
    assert c.post("/api/blacklist", json={"name": "Meta"}).status_code in (200, 201)
    assert [x["name"] for x in c.get("/api/blacklist").json()] == ["Meta"]
    _switch(c, b)
    assert c.get("/api/blacklist").json() == []
    # The same company can be blacklisted independently by the other person.
    assert c.post("/api/blacklist", json={"name": "Meta, Inc."}).status_code in (200, 201)
    _switch(c, a)
    assert [x["name"] for x in c.get("/api/blacklist").json()] == ["Meta"]


def test_preferences_are_per_profile(setup):
    c, _, a, b, _job = setup
    assert c.patch("/api/preferences", json={"ghosted_after_days": 20}).status_code == 200
    _switch(c, b)
    assert c.get("/api/preferences").json()["ghosted_after_days"] == 45
    c.patch("/api/preferences", json={"ghosted_after_days": 90})
    _switch(c, a)
    assert c.get("/api/preferences").json()["ghosted_after_days"] == 20


def test_drafts_are_per_profile(setup, monkeypatch):
    c, _, a, b, job = setup
    monkeypatch.setattr(pdf, "render_to_pdf", lambda _url: b"%PDF fake")
    c.post(f"/api/jobs/{job}/draft", json={"resume_md": "# A resume\n"})
    _switch(c, b)
    draft_b = c.get(f"/api/jobs/{job}/draft?include_markdown=true").json()
    assert draft_b["has_resume_md"] is False
    c.post(f"/api/jobs/{job}/draft", json={"resume_md": "# B resume\n"})
    _switch(c, a)
    draft_a = c.get(f"/api/jobs/{job}/draft?include_markdown=true").json()
    assert draft_a["resume_md"].startswith("# A resume")
    assert (settings.applications_dir / f"profile-{b}" / str(job) / "resume.md").exists()


def test_pinned_profile_wins_over_the_active_one(setup):
    # A background task started for A keeps reading/writing A after a switch.
    c, engine, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    _switch(c, b)
    token = current_profile_id.set(a)
    try:
        with Session(engine) as s:
            assert session_profile_id(s) == a
            j = s.get(JobPosting, job)
            assert j.application is not None and j.application.status.value == "applied"
    finally:
        current_profile_id.reset(token)
    with Session(engine) as s:
        assert s.get(JobPosting, job).application is None


def test_running_task_dedupe_is_per_profile():
    state = tasks.TaskState(id="t-a", kind="score_pending", total=1, profile_id=1)
    with tasks._lock:
        tasks._tasks["t-a"] = state
    try:
        assert tasks.active_task("score_pending", 1) is state
        assert tasks.active_task("score_pending", 2) is None
    finally:
        with tasks._lock:
            tasks._tasks.pop("t-a", None)


def test_deleting_a_profile_removes_only_its_own_data(setup, monkeypatch):
    c, engine, a, b, job = setup
    monkeypatch.setattr(pdf, "render_to_pdf", lambda _url: b"%PDF fake")
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    _switch(c, b)
    c.patch(f"/api/jobs/{job}/status", json={"status": "interested"})
    c.post(f"/api/jobs/{job}/score", json={"score": 50, "rubric": {}})
    c.post("/api/blacklist", json={"name": "Initech"})
    c.patch("/api/preferences", json={"ghosted_after_days": 90})
    c.post(f"/api/jobs/{job}/draft", json={"resume_md": "# B\n"})
    _switch(c, a)

    assert c.delete(f"/api/search-profiles/{b}").status_code == 204

    with Session(engine) as s:
        for model in (Application, MatchScore, JobProfileLink):
            rows = s.exec(select(model).execution_options(all_profiles=True)).all()
            assert {r.profile_id for r in rows} <= {a}, model.__name__
        assert s.exec(select(AppSetting).where(AppSetting.key.startswith(f"pref:{b}:"))).all() == []
    assert not (settings.applications_dir / f"profile-{b}").exists()
    # A's data and the shared posting survive.
    assert c.get(f"/api/jobs/{job}").json()["application"]["status"] == "applied"


def test_changes_to_what_a_profile_accepts_rematch_it(setup, rematches):
    c, _engine, a, _b, _job = setup
    rematches.clear()

    c.put("/api/search-profile", json={"seniority_terms": ["staff"], "required_tech": ["go"]})
    new = c.post("/api/search-profiles", json={"name": "Third"}).json()["id"]
    entry = c.post("/api/blacklist", json={"name": "Initech"}).json()
    c.delete(f"/api/blacklist/{entry['id']}")
    assert rematches == [a, new, a, a]


def test_prune_keeps_a_jd_another_profile_applied_to(setup):
    # Prune reads statuses across every profile: A archiving a job must not
    # blank the description of the posting B applied to.
    from job_applier.maintenance import prune_old_postings

    c, engine, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "archived"})
    _switch(c, b)
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    with Session(engine) as s:
        assert prune_old_postings(s).lightened == 0
        assert s.get(JobPosting, job).description

    _switch(c, a)  # B gives up too: now every tracker has it closed
    _switch(c, b)
    c.patch(f"/api/jobs/{job}/status", json={"status": "rejected"})
    with Session(engine) as s:
        assert prune_old_postings(s).lightened == 1


def test_queue_tabs_follow_the_profiles_own_verdicts(setup):
    # The queue and its "Needs review" tab come from the active profile's link
    # verdicts, and what the API reports for a posting is that verdict too.
    c, engine, a, _b, passed_job = setup
    with Session(engine) as s:
        ids = {}
        for sid in ("manual", "dropped"):
            j = JobPosting(
                source="test", source_id=sid, url=f"https://example.com/{sid}",
                title=f"Senior {sid}", description="x", dedupe_hash=f"h-{sid}",
            )
            s.add(j)
            s.flush()
            ids[sid] = j.id
        s.add(JobProfileLink(job_id=ids["manual"], profile_id=a,
                             filter_status=FilterStatus.manual, filter_reason="tech only implied"))
        s.add(JobProfileLink(job_id=ids["dropped"], profile_id=a,
                             filter_status=FilterStatus.dropped, filter_reason="no seniority term"))
        s.commit()

    def queue(**params):
        return {j["id"]: j for j in c.get("/api/jobs", params=params).json()}

    assert set(queue()) == {passed_job}
    manual = queue(filter_status="manual")
    assert set(manual) == {ids["manual"]}
    assert (manual[ids["manual"]]["filter_status"], manual[ids["manual"]]["filter_reason"]) == (
        "manual", "tech only implied",
    )
    counts = c.get("/api/jobs/status-counts", params={"filter_status": "manual"}).json()
    assert counts["counts"]["none"] == 1 and sum(counts["counts"].values()) == 1
    # A posting this profile never matched reports no verdict, not "passed".
    with Session(engine) as s:
        unseen = JobPosting(source="test", source_id="u", url="https://example.com/u",
                            title="Senior Unseen", description="x", dedupe_hash="h-u")
        s.add(unseen)
        s.commit()
        unseen_id = unseen.id
    assert c.get(f"/api/jobs/{unseen_id}").json()["filter_status"] is None


def test_job_detail_shows_what_other_profiles_did_with_it(setup):
    # Read-only awareness across profiles: B sees that A applied, A sees nothing
    # from B until B acts, and the machine's "archived" bucket isn't news.
    c, _engine, a, b, job = setup
    assert c.get(f"/api/jobs/{job}").json()["other_profiles"] == []
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})

    _switch(c, b)
    others = c.get(f"/api/jobs/{job}").json()["other_profiles"]
    assert others == [{"profile_id": a, "name": "Default", "status": "applied"}]
    c.patch(f"/api/jobs/{job}/status", json={"status": "archived"})

    _switch(c, a)
    assert c.get(f"/api/jobs/{job}").json()["other_profiles"] == []


def test_prune_keeps_a_jd_still_open_in_another_profiles_queue(setup):
    # B hasn't touched the job (a link, no Application): A rejecting it must not
    # blank the description B is about to read.
    from job_applier.maintenance import prune_old_postings

    c, engine, _a, _b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "rejected"})
    with Session(engine) as s:
        assert prune_old_postings(s).lightened == 0
        assert s.get(JobPosting, job).description


def test_print_html_serves_the_requested_profiles_draft(setup, monkeypatch):
    # The PDF driver passes profile_id so a mid-render switch still prints the
    # starting profile's markdown, never the newly active person's.
    c, _, a, b, job = setup
    monkeypatch.setattr(pdf, "render_to_pdf", lambda _url: b"%PDF fake")
    c.post(f"/api/jobs/{job}/draft", json={"resume_md": "# Only A\n"})
    _switch(c, b)
    r = c.get(f"/api/jobs/{job}/draft/resume/print.html?profile_id={a}")
    assert r.status_code == 200 and "Only A" in r.text
    assert c.get(f"/api/jobs/{job}/draft/resume/print.html").status_code == 404


def test_a_migrated_preference_falls_back_to_the_shared_value(setup):
    from job_applier.contracts import GHOSTED_AFTER_DAYS_KEY

    c, engine, _a, _b, _job = setup
    with Session(engine) as s:
        s.add(AppSetting(key=GHOSTED_AFTER_DAYS_KEY, value="20"))
        s.commit()
    assert c.get("/api/preferences").json()["ghosted_after_days"] == 20
    c.patch("/api/preferences", json={"ghosted_after_days": 30})
    assert c.get("/api/preferences").json()["ghosted_after_days"] == 30
    with Session(engine) as s:
        assert s.get(AppSetting, GHOSTED_AFTER_DAYS_KEY).value == "20"  # legacy row untouched


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("patch", "/api/search-profiles/{a}", {"name": "   "}, 422),
        ("patch", "/api/search-profiles/9999", {"name": "X"}, 404),
        ("patch", "/api/search-profiles/{a}", {"resume_id": 9999}, 404),
        ("post", "/api/search-profiles", {"name": "   "}, 422),
        ("post", "/api/search-profiles", {"name": "X", "clone_from": 9999}, 404),
        ("post", "/api/search-profiles/9999/activate", None, 404),
        ("delete", "/api/search-profiles/9999", None, 404),
    ],
)
def test_profile_api_error_mapping(setup, method, path, body, status):
    c, _, a, _b, _job = setup
    kwargs = {"json": body} if body is not None else {}
    assert getattr(c, method)(path.format(a=a), **kwargs).status_code == status


def test_profile_api_renames(setup):
    c, _, _a, b, _job = setup
    r = c.patch(f"/api/search-profiles/{b}", json={"name": " Sam "})
    assert r.status_code == 200 and r.json()["name"] == "Sam"
    assert "Sam" in [p["name"] for p in c.get("/api/search-profiles").json()]


def test_deleting_a_profile_whose_drafts_are_locked_changes_nothing(setup, monkeypatch):
    # Windows refuses to move a folder with a PDF open in a viewer: the delete
    # must fail cleanly (409, a reason) before anything is removed.
    from job_applier import drafts

    def locked(_pid):
        raise PermissionError("in use")

    c, _, _a, b, _job = setup
    monkeypatch.setattr(drafts, "set_aside_profile_drafts", locked)
    r = c.delete(f"/api/search-profiles/{b}")
    assert r.status_code == 409 and "close any of its open PDFs" in r.json()["detail"]
    assert b in [p["id"] for p in c.get("/api/search-profiles").json()]


def test_a_background_task_runs_as_the_profile_that_started_it():
    import threading

    seen: list[int | None] = []
    done = threading.Event()

    def fn(_state):
        seen.append(current_profile_id.get())
        done.set()

    tasks.start_task("unit_pin", 1, fn, profile_id=7)
    assert done.wait(5)
    # The same lane's worker runs the next task: the pin must not carry over.
    done.clear()
    tasks.start_task("unit_pin", 1, fn)
    assert done.wait(5)
    assert seen == [7, None]
    assert current_profile_id.get() is None
