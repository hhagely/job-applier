"""Canary tests for the status / kind contract that is hand-mirrored across the
Python and TypeScript sides (there is no shared codegen).

The failure modes these guard against are silent: a status added to the Python
``ApplicationStatus`` enum but not to ``APPLICATION_STATUSES`` in the frontend
renders as an unstyled tag and slips through the UI status filters; the reverse
makes the new status un-settable from the UI. A score kind or task status that
drifts from the ``Literal`` the frontend narrows to fails the same way.

These tests pin all three to a documented order; the matching frontend guard is
``web/src/lib/__tests__/statusContract.test.ts``.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

from job_applier.api.schemas import ScoreOut, StatusFacet, TaskOut
from job_applier.models.db import ApplicationStatus

# The canonical pipeline order. Mirrored verbatim in web/src/lib/api.ts
# (APPLICATION_STATUSES) and asserted there by a vitest of the same name.
EXPECTED_STATUSES = [
    "new",
    "interested",
    "drafted",
    "applied",
    "screening",
    "interviewing",
    "rejected",
    "no_response",
    "archived",
]

_API_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "api.ts"


def test_python_enum_matches_canonical_order():
    assert [s.value for s in ApplicationStatus] == EXPECTED_STATUSES


def test_typescript_array_matches_python_enum():
    """Parse ``APPLICATION_STATUSES`` out of api.ts and assert it equals the enum.

    A genuine cross-language drift alarm: change the Python enum without updating
    the TS array (or vice-versa) and this fails, from the backend CI job.
    """
    text = _API_TS.read_text(encoding="utf-8")
    match = re.search(
        r"APPLICATION_STATUSES:\s*ApplicationStatus\[\]\s*=\s*\[(.*?)\]", text, re.S
    )
    assert match, "could not locate APPLICATION_STATUSES in web/src/lib/api.ts"
    ts_values = re.findall(r"'([^']+)'", match.group(1))
    assert ts_values == [s.value for s in ApplicationStatus]


def test_score_kind_literal_is_baseline_or_tailored():
    args = typing.get_args(ScoreOut.model_fields["score_kind"].annotation)
    assert set(args) == {"baseline", "tailored"}


def test_task_status_literal_matches_runner():
    args = typing.get_args(TaskOut.model_fields["status"].annotation)
    assert set(args) == {"running", "done", "error"}


def test_status_facet_is_every_status_plus_none():
    """``StatusFacet`` is a query param on /api/jobs, so it must track the enum.

    A status added to ``ApplicationStatus`` but not here would 422 the moment the
    queue tried to filter on its chip — the filter would look present but be
    unusable.
    """
    assert [f.value for f in StatusFacet] == EXPECTED_STATUSES + ["none"]


def test_typescript_status_facet_matches_python():
    """``STATUS_FACETS`` in api.ts is spread from APPLICATION_STATUSES + 'none'.

    Assert the spread is still what it claims, so the TS union that the queue's
    URL parsing validates against cannot drift from the Python enum.
    """
    text = _API_TS.read_text(encoding="utf-8")
    match = re.search(r"STATUS_FACETS:\s*StatusFacet\[\]\s*=\s*\[(.*?)\];", text, re.S)
    assert match, "could not locate STATUS_FACETS in web/src/lib/api.ts"
    body = match.group(1)
    assert "...APPLICATION_STATUSES" in body
    assert re.findall(r"'([^']+)'", body) == ["none"]
