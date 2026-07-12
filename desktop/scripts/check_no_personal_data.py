#!/usr/bin/env python3
"""Data-isolation guard (Phase 7, Workstream A2).

The author uses this app, so the repo holds real personal data: ``data/jobs.db``,
``data/resumes/``, and ``applications/<id>/`` tailored drafts. NONE of it may end up
in a distributed installer. Today that is true by construction (PyInstaller collects
only ``job_applier`` code + ``ai/prompts/*.md``; electron-builder bundles only shell
code + the frozen backend + ``web/build``; releases build from a clean CI checkout).
This script turns "we structured it right" into "the pipeline refuses to ship personal
data": it scans a packaged app tree and FAILS the build if any personal-data payload is
found.

Run after ``electron-builder`` (see the ``dist`` make target and ``release.yml``):

    python desktop/scripts/check_no_personal_data.py desktop/dist

Cross-platform + stdlib-only so it runs identically on the Linux and Windows CI
runners (Windows has no ``make``). Exit 0 = clean, exit 1 = personal data found.
"""

from __future__ import annotations

import sys
from pathlib import Path

# SQLite databases (the jobs DB + its journal/WAL sidecars and the author's .bak).
_DB_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm")


def _looks_like_db(p: Path) -> bool:
    name = p.name.lower()
    return name.endswith(_DB_SUFFIXES) or name.endswith(".db.bak")


def _is_resumes_payload(d: Path) -> bool:
    """A directory literally named ``resumes`` that holds uploaded resume files."""
    if d.name.lower() != "resumes":
        return False
    return any(child.is_file() for child in d.iterdir())


def _is_applications_payload(d: Path) -> bool:
    """A directory named ``applications`` with a numeric ``<job_id>/`` subdir that
    holds draft files (resume.md / cover_letter.pdf / ...)."""
    if d.name.lower() != "applications":
        return False
    for child in d.iterdir():
        if child.is_dir() and child.name.isdigit():
            if any(g.is_file() for g in child.iterdir()):
                return True
    return False


def scan(root: Path) -> list[str]:
    """Return human-readable descriptions of every personal-data payload under root."""
    findings: list[str] = []
    if not root.exists():
        return findings
    # Include root itself: rglob yields only descendants, so a root passed
    # directly (e.g. `applications`) would otherwise never be tested as a payload.
    for p in [root, *root.rglob("*")]:
        try:
            if p.is_file() and _looks_like_db(p):
                findings.append(f"SQLite database: {p}")
            elif p.is_dir() and _is_resumes_payload(p):
                findings.append(f"resumes/ payload: {p}")
            elif p.is_dir() and _is_applications_payload(p):
                findings.append(f"applications/<id>/ drafts: {p}")
        except OSError:
            # Unreadable entry (broken symlink, permissions) can't hold personal
            # data we're responsible for; skip rather than crash the guard.
            continue
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("desktop/dist")]
    all_findings: list[str] = []
    for root in roots:
        all_findings.extend(scan(root))

    scanned = ", ".join(str(r) for r in roots)
    if all_findings:
        print("DATA-ISOLATION GUARD FAILED: personal data found in the packaged app.")
        print(f"  scanned: {scanned}")
        for f in sorted(set(all_findings)):
            print(f"  - {f}")
        print(
            "\nRefusing to ship. A build must never sweep data/ or applications/ into\n"
            "the installer. If a config change (extraResources / files / .spec datas)\n"
            "widened the bundle, revert it; releases build from a clean checkout where\n"
            "this data does not exist."
        )
        return 1

    print(f"Data-isolation guard passed: no personal data in {scanned}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
