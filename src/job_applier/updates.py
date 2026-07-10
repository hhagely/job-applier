"""In-app update check (Phase 7, Workstream E).

Compares the running version (``job_applier.__version__`` — the single source of
truth, Workstream B) against the latest GitHub Release. No signed auto-update: the
UI just shows a banner linking to Releases. This runs server-side so it works in
browser dev and is unit-testable by mocking ``httpx``.

Design constraints from the spec:
- Cache in-process (6h TTL) so we don't hit GitHub on every page load.
- Fail soft: any network error / rate-limit returns ``update_available: False``
  rather than raising, so the banner simply doesn't appear.
- No token: unauthenticated latest-release reads are fine for a single-user app.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from job_applier import __version__

_LATEST_RELEASE_URL = "https://api.github.com/repos/hhagely/job-applier/releases/latest"
_RELEASES_PAGE = "https://github.com/hhagely/job-applier/releases/latest"
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6h

# Module-level cache: (monotonic_expiry, result_dict).
_cache: tuple[float, dict] | None = None


@dataclass(frozen=True)
class _Version:
    parts: tuple[int, ...]

    @classmethod
    def parse(cls, raw: str) -> "_Version":
        """Parse a version / tag like ``v1.2.3`` or ``0.1.0`` into comparable ints.

        Leading ``v`` is stripped; a pre-release/build suffix (``-rc1``, ``+meta``)
        is dropped so ``1.2.0-rc1`` compares as ``1.2.0``. Unparseable => ``(0,)``.
        """
        s = (raw or "").strip().lstrip("vV")
        for sep in ("-", "+"):
            s = s.split(sep, 1)[0]
        nums: list[int] = []
        for chunk in s.split("."):
            if chunk.isdigit():
                nums.append(int(chunk))
            else:
                break
        return cls(tuple(nums) or (0,))

    def __lt__(self, other: "_Version") -> bool:
        a, b = self.parts, other.parts
        length = max(len(a), len(b))
        return a + (0,) * (length - len(a)) < b + (0,) * (length - len(b))


def _soft_result(latest: str | None = None) -> dict:
    """A no-update payload, used both when up-to-date and on any failure."""
    return {
        "current": __version__,
        "latest": latest,
        "update_available": False,
        "url": _RELEASES_PAGE,
    }


def _fetch_latest_tag() -> str | None:
    resp = httpx.get(
        _LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=6.0,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json().get("tag_name")


def check_for_update(*, force: bool = False) -> dict:
    """Return ``{current, latest, update_available, url}``.

    Cached for 6h and fail-soft: network errors, rate limits, or a malformed
    response all yield ``update_available: False`` so the banner just stays hidden.
    """
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now < _cache[0]:
        return _cache[1]

    try:
        latest = _fetch_latest_tag()
    except (httpx.HTTPError, ValueError, KeyError):
        # Do not cache transient failures for the full TTL; a short backoff keeps
        # the banner responsive once connectivity returns.
        result = _soft_result()
        _cache = (now + 5 * 60, result)
        return result

    if not latest:
        result = _soft_result()
    else:
        available = _Version.parse(__version__) < _Version.parse(latest)
        result = {
            "current": __version__,
            "latest": latest,
            "update_available": available,
            "url": _RELEASES_PAGE,
        }
    _cache = (now + _CACHE_TTL_SECONDS, result)
    return result


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None
