"""Company-board refresh: lock behaviour and feed-payload tolerance.

``refresh_slugs`` runs on the ``net`` task lane, so it is *designed* to overlap
with the user clicking around the app. Two ways that used to go wrong, both
covered here:

- It held one session open across the whole run. The first insert took SQLite's
  single write lock, and the following minutes of verification HTTP kept it —
  every other write in the app then blocked on ``busy_timeout`` and failed with
  "database is locked" (a 503 in the UI). Same class of bug as the one 5fe4ec9
  fixed in ``run_ingest``; the invariant is "never hold a DB transaction across
  network I/O".
- It iterated the SimplifyJobs feed payload without checking its shape, so an
  error document that happened to parse as JSON killed the run before a single
  source was checked.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.models import SourceSlug
from job_applier.sources import refresh as refresh_mod

# Every source refresh_slugs discovers candidates for, plus Workday (re-verified
# only). Used to build stub candidate sets.
_CANDIDATE_SOURCES = ("greenhouse", "lever", "workable", "smartrecruiters", "ashby")


def _engine(tmp_path):
    """A file-backed SQLite engine with SQLite's *default* locking behaviour.

    File-backed so two connections genuinely contend (an in-memory StaticPool
    shares one connection and could never observe a lock). Deliberately *not* the
    app's hardened engine: no WAL, so a read transaction held across the network
    passes trips this too, and a short busy timeout so a held lock fails in
    milliseconds instead of stalling the suite.
    """
    e = create_engine(
        f"sqlite:///{tmp_path / 'refresh.db'}", connect_args={"timeout": 0.5}
    )
    SQLModel.metadata.create_all(e)
    return e


def _candidates(**by_source: set[str]) -> dict[str, set[str]]:
    found = {source: set() for source in _CANDIDATE_SOURCES}
    found.update(by_source)
    return found


def _stub_feed(monkeypatch, candidates: dict[str, set[str]]) -> None:
    monkeypatch.setattr(
        refresh_mod, "_fetch_candidates_from_simplify", lambda: candidates
    )


def _stub_verifiers(monkeypatch, verify) -> None:
    """Point all three verifiers at ``verify(slugs, *args)`` — no network."""
    for name in ("_verify_many", "_verify_workable", "_verify_workday"):
        monkeypatch.setattr(refresh_mod, name, verify)


def _seed(eng, rows: list[SourceSlug]) -> None:
    with Session(eng) as s:
        s.add_all(rows)
        s.commit()


# ---- Bug 1: no write lock is held across the network passes ----------------


def test_no_db_lock_is_held_during_verification(tmp_path, monkeypatch):
    """While any verify pass runs, an independent connection must be able to write.

    This is the regression guard: the verifier stubs stand in for the minutes of
    HTTP the real ones spend, and each one tries a small write of its own from a
    second session. Under the old shape (one session open from the first insert
    to the final commit) those writes fail with "database is locked" — exactly
    what a status change from the queue did while a refresh ran.
    """
    eng = _engine(tmp_path)
    monkeypatch.setattr(refresh_mod, "engine", lambda: eng)
    # Existing rows so the re-verification passes have real work (and real row
    # mutations) to interleave with.
    _seed(
        eng,
        [
            SourceSlug(source="greenhouse", slug="acme"),
            SourceSlug(source="lever", slug="globex"),
            SourceSlug(source="ashby", slug="Initech"),
            SourceSlug(source="workday", slug="acme|wd1|Careers"),
            SourceSlug(source="workable", slug="hooli"),
            SourceSlug(source="smartrecruiters", slug="Umbrella"),
        ],
    )
    _stub_feed(monkeypatch, _candidates(greenhouse={"newco"}, lever={"newlever"}))

    locked: list[str] = []
    passes: list[int] = []

    def _verify(slugs, *args, **kwargs):
        n = len(passes)
        passes.append(n)
        try:
            with Session(eng) as other:
                other.add(SourceSlug(source="probe", slug=f"probe-{n}"))
                other.commit()
        except OperationalError as exc:  # "database is locked"
            locked.append(f"pass {n}: {exc}")
        return [(slug, True, 1, None) for slug in slugs]

    _stub_verifiers(monkeypatch, _verify)

    refresh_mod.refresh_slugs(reverify_existing=True)

    assert locked == []
    # Five discovery passes plus six re-verification passes, each probed.
    assert len(passes) == 11
    with Session(eng) as s:
        probes = s.exec(select(SourceSlug).where(SourceSlug.source == "probe")).all()
    assert len(probes) == 11


@pytest.mark.parametrize(
    ("reverify", "expected"),
    [(False, refresh_mod.REFRESH_STEPS), (True, refresh_mod.REFRESH_STEPS_REVERIFY)],
)
def test_progress_steps_match_the_advertised_total(
    tmp_path, monkeypatch, reverify, expected
):
    """The API sizes the task bar from REFRESH_STEPS/REFRESH_STEPS_REVERIFY before
    the run starts, so the number of _step() calls has to match exactly."""
    eng = _engine(tmp_path)
    monkeypatch.setattr(refresh_mod, "engine", lambda: eng)
    _seed(eng, [SourceSlug(source="greenhouse", slug="acme")])
    _stub_feed(monkeypatch, _candidates(greenhouse={"newco"}))
    _stub_verifiers(
        monkeypatch, lambda slugs, *a, **k: [(s, True, 1, None) for s in slugs]
    )

    seen: list[tuple[int, int, str]] = []
    refresh_mod.refresh_slugs(
        reverify_existing=reverify,
        progress_cb=lambda done, total, label: seen.append((done, total, label)),
    )

    assert [done for done, _, _ in seen] == list(range(1, expected + 1))
    assert {total for _, total, _ in seen} == {expected}
    assert seen[0][2] == "fetched candidate list"


def test_run_commits_new_slugs_and_preserves_user_added_rows(tmp_path, monkeypatch):
    """A full re-verify run persists what it discovered, disables what died, and
    leaves a hand-added row's ``added_by_user``/``label`` untouched."""
    eng = _engine(tmp_path)
    monkeypatch.setattr(refresh_mod, "engine", lambda: eng)
    _seed(
        eng,
        [
            SourceSlug(
                source="greenhouse", slug="acme", added_by_user=True, label="Acme Corp"
            ),
            SourceSlug(source="lever", slug="dead-co"),
        ],
    )
    # "acme" is already stored, so it must not be re-added as a discovery.
    _stub_feed(monkeypatch, _candidates(greenhouse={"newco", "acme"}))
    _stub_verifiers(
        monkeypatch,
        lambda slugs, *a, **k: [
            (s, False, None, "HTTP 404") if s.startswith("dead") else (s, True, 3, None)
            for s in slugs
        ],
    )

    stats = refresh_mod.refresh_slugs(reverify_existing=True)

    with Session(eng) as s:
        rows = {(r.source, r.slug): r for r in s.exec(select(SourceSlug)).all()}
        assert set(rows) == {
            ("greenhouse", "acme"),
            ("greenhouse", "newco"),
            ("lever", "dead-co"),
        }
        discovered = rows[("greenhouse", "newco")]
        assert discovered.enabled is True
        assert discovered.last_job_count == 3
        assert discovered.added_by_user is False

        user_added = rows[("greenhouse", "acme")]
        assert user_added.added_by_user is True
        assert user_added.label == "Acme Corp"
        assert user_added.enabled is True
        assert user_added.last_error is None
        assert user_added.last_fetched_at is not None

        dead = rows[("lever", "dead-co")]
        assert dead.enabled is False
        assert dead.last_error == "HTTP 404"

    assert stats.gh_added == 1
    assert stats.gh_reverified == 1
    assert stats.lv_reverified == 1
    assert stats.lv_disabled == 1


# ---- Bug 2: the feed payload is untrusted ----------------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, _url):
        return _FakeResp(self._payload)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # A normal feed: the shape we actually expect.
        ([{"url": "https://boards.greenhouse.io/acme"}], {"acme"}),
        # An error document / reshaped envelope. Iterating a dict yields its keys,
        # which have no .get() — that used to abort the whole refresh run.
        ({"message": "Not Found"}, set()),
        # Junk entries alongside a good one: skip the junk, keep the board.
        (
            ["oops", 7, None, {"url": "https://boards.greenhouse.io/acme"}, {}],
            {"acme"},
        ),
        # A non-string url is just as unusable as a missing one.
        ([{"url": 42}], set()),
    ],
    ids=["list-of-dicts", "dict-envelope", "junk-elements", "non-string-url"],
)
def test_feed_parse_tolerates_unexpected_payloads(monkeypatch, payload, expected):
    monkeypatch.setattr(
        refresh_mod.httpx, "Client", lambda **kw: _FakeClient(payload)
    )
    found = refresh_mod._fetch_candidates_from_simplify()
    assert found["greenhouse"] == expected


def test_feed_parse_warns_when_the_envelope_is_not_a_list(monkeypatch, caplog):
    monkeypatch.setattr(
        refresh_mod.httpx, "Client", lambda **kw: _FakeClient({"message": "Not Found"})
    )
    refresh_mod._fetch_candidates_from_simplify()
    assert "expected a list" in caplog.text
