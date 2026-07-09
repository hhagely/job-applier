#!/usr/bin/env python3
"""Version stamp (Phase 7, Workstream B).

The single source of truth for the app version is ``src/job_applier/__init__.py``
``__version__`` (Python is the backend of record). electron-builder derives the
installer filename and the ``window.desktop.version`` bridge from
``desktop/package.json``, so this script copies ``__version__`` into that file
before packaging. Run by ``make stamp-version`` (invoked from ``make dist``) and by
the release workflow, so the installer name, the desktop bridge, and the in-app
update check all agree.

stdlib-only + cross-platform (Windows CI has no ``make``):

    python desktop/scripts/stamp_version.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT = _REPO_ROOT / "src" / "job_applier" / "__init__.py"
_PACKAGE_JSON = _REPO_ROOT / "desktop" / "package.json"


def read_version() -> str:
    text = _INIT.read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"Could not find __version__ in {_INIT}")
    return m.group(1)


def stamp() -> bool:
    """Write the canonical version into desktop/package.json's top ``version`` field.

    A targeted text substitution (not a JSON round-trip) so nothing else in the file
    reformats. Returns True if the file changed.
    """
    version = read_version()
    text = _PACKAGE_JSON.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1
    )
    if count == 0:
        raise SystemExit(f'Could not find a "version" field in {_PACKAGE_JSON}')
    changed = new_text != text
    if changed:
        _PACKAGE_JSON.write_text(new_text, encoding="utf-8")
    return changed


def main() -> int:
    version = read_version()
    changed = stamp()
    status = "stamped" if changed else "already current"
    print(f"desktop/package.json version {status}: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
