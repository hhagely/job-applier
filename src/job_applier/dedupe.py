"""Fingerprinting + normalization for cross-source dedupe.

Pure functions (no DB, no I/O) shared by the ingest pipeline (``ingest``) and the
offline backfills (``maintenance``). Split out of ``ingest`` so the live pipeline,
the dedupe algorithms, and the batch maintenance jobs are three cohesive modules
rather than one 600-line file.
"""

from __future__ import annotations

import hashlib
import re

from job_applier.contracts import RawJob


def dedupe_hash(raw: RawJob) -> str:
    h = hashlib.sha256()
    h.update(raw.source.encode())
    h.update(b"|")
    h.update(raw.source_id.encode())
    return h.hexdigest()


_LOCATION_SUFFIX_FALLBACK = re.compile(
    r"\s*[\-–—]\s*"
    r"[^,\-–—]{1,40},\s*[^,\-–—]{1,40}"
    r"(?:,\s*[^,\-–—]{1,40})?"
    r"\s*$"
)


def _strip_location_suffix(title: str, location: str | None) -> str:
    """Remove a trailing " - {location}" from a title.

    Speechify and other Greenhouse posters fan a single role out into one
    posting per city; the title gets " - {City}, {State}, {Country}" appended
    and the `location` field carries that same suffix verbatim. Stripping it
    lets dedupe see the underlying role.
    """
    if location:
        pattern = re.compile(
            r"\s*[\-–—]\s*" + re.escape(location.strip()) + r"\s*$",
            re.IGNORECASE,
        )
        stripped = pattern.sub("", title)
        if stripped != title:
            return stripped
    return _LOCATION_SUFFIX_FALLBACK.sub("", title)


def normalize_title(title: str, location: str | None = None) -> str:
    return " ".join(_strip_location_suffix(title, location).lower().split())


_COMPANY_SUFFIXES = re.compile(
    r"[,\s]+(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|"
    r"plc|gmbh|s\.?a\.?|s\.?l\.?|pty|ag|nv|bv)\.?$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TITLE_NOISE = re.compile(
    r"\b(remote|us|usa|united\s+states|anywhere|global|emea|apac|americas|"
    r"\(remote\)|\(us\)|\(usa\)|m/?f/?d|f/?m/?d|h/?f|m/?w/?d)\b",
    re.IGNORECASE,
)


def normalize_company(name: str) -> str:
    """Canonical company-name key: strip a legal suffix (Inc/LLC/GmbH/...) then
    lowercase and drop every non-alphanumeric char. Shared by cross-source
    dedupe and the user company blacklist so "Meta", "Meta Inc", and
    "Meta, Inc." all collapse to one key.
    """
    s = (name or "").strip()
    s = _COMPANY_SUFFIXES.sub("", s)
    s = _NON_ALNUM.sub("", s.lower())
    return s


# Back-compat alias for internal callers that predate the public name.
_normalize_company = normalize_company


def _normalize_title(title: str, location: str | None = None) -> str:
    s = _strip_location_suffix(title or "", location).lower()
    s = re.sub(r"\bsr\.?\b", "senior", s)
    s = re.sub(r"\bjr\.?\b", "junior", s)
    s = re.sub(r"\beng\.?\b", "engineer", s)
    s = re.sub(r"\bdev\.?\b", "developer", s)
    s = _TITLE_NOISE.sub("", s)
    s = _NON_ALNUM.sub("", s)
    return s


_HTML_TAG = re.compile(r"<[^>]+>")
_TOKEN = re.compile(r"[a-z0-9]+")

# Pre-fingerprint floor: SimHash on a few dozen tokens collides too readily.
# 200 chars of cleaned text is roughly the shortest "real" JD we see.
JD_MIN_CHARS = 200

# Shingle size: 3-grams of tokens. Smaller catches more boilerplate as a match,
# larger needs more verbatim overlap. 3 is a common starting point.
JD_SHINGLE_K = 3

# Hamming-distance threshold (out of 64 bits). 0-3 is "near-identical" for
# SimHash on text; revisit after sweeping real data.
JD_HAMMING_THRESHOLD = 3


def _jd_clean_text(description: str) -> str:
    stripped = _HTML_TAG.sub(" ", description or "")
    return stripped.lower()


def _jd_shingles(text: str) -> list[str]:
    tokens = _TOKEN.findall(text)
    if len(tokens) < JD_SHINGLE_K:
        return []
    return [
        " ".join(tokens[i : i + JD_SHINGLE_K])
        for i in range(len(tokens) - JD_SHINGLE_K + 1)
    ]


def jd_simhash(description: str) -> str | None:
    """64-bit SimHash of a JD as a 16-char hex string.

    Returns None when the cleaned text is too thin to fingerprint without
    risking false collisions.
    """
    text = _jd_clean_text(description)
    if len(text) < JD_MIN_CHARS:
        return None
    shingles = _jd_shingles(text)
    if not shingles:
        return None

    bits = [0] * 64
    for shingle in shingles:
        h = int.from_bytes(
            hashlib.blake2b(shingle.encode(), digest_size=8).digest(),
            "big",
        )
        for i in range(64):
            if (h >> i) & 1:
                bits[i] += 1
            else:
                bits[i] -= 1

    fingerprint = 0
    for i, v in enumerate(bits):
        if v > 0:
            fingerprint |= 1 << i
    return f"{fingerprint:016x}"


def jd_hamming_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def cross_source_hash(raw: RawJob) -> str | None:
    """Hash a normalized (company, title) so the same role from different
    sources collapses to one row. Returns None if either field is too thin
    to fingerprint reliably (avoids false-positive collisions)."""
    company = _normalize_company(raw.company_name)
    title = _normalize_title(raw.title, raw.location)
    if len(company) < 2 or len(title) < 4:
        return None
    h = hashlib.sha256()
    h.update(company.encode())
    h.update(b"|")
    h.update(title.encode())
    return h.hexdigest()
