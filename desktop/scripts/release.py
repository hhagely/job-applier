#!/usr/bin/env python3
"""Cut a release (Phase 7): bump the version, commit, tag, and push.

Pushing a ``v*`` tag is what triggers ``.github/workflows/release.yml`` (per-OS
build -> data-isolation guard -> draft GitHub Release). This script does the local
half of that in one shot, with guards so it fails loudly instead of doing the wrong
thing:

    make release VERSION=0.2.0

Pass ``DRY_RUN=1`` (``--dry-run``) to run every precondition check and print the
plan without editing, committing, tagging, or pushing anything.

Steps:
  1. validate the version and that the working tree is clean,
  2. rewrite __version__ (src/job_applier/__init__.py) + version (pyproject.toml),
  3. stamp desktop/package.json from __version__ (reuses stamp_version.py),
  4. commit "Release vX.Y.Z", create tag vX.Y.Z, push the branch + the tag.

After CI finishes, publish the resulting DRAFT release from the Releases page (see
.github/release-notes-template.md).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Reuse the single-source-of-truth stamp logic.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stamp_version  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT = _REPO_ROOT / "src" / "job_applier" / "__init__.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# X.Y.Z with an optional pre-release suffix (e.g. 0.2.0-rc1).
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")


def _die(msg: str) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _git(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=capture,
        check=True,
    )
    return (result.stdout or "").strip() if capture else ""


def _replace_once(path: Path, pattern: str, repl: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if count == 0:
        _die(f"could not find {what} in {path}")
    path.write_text(new_text, encoding="utf-8")


def main(argv: list[str]) -> int:
    args = argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if len(args) != 1:
        _die("usage: release.py X.Y.Z [--dry-run]  (or: make release VERSION=X.Y.Z [DRY_RUN=1])")
    version = args[0].lstrip("vV")
    if not _VERSION_RE.match(version):
        _die(f"'{version}' is not a valid version (expected X.Y.Z[-suffix])")
    tag = f"v{version}"

    # --- preconditions (all read-only; run in dry-run too) ------------------
    if _git("status", "--porcelain", capture=True):
        _die("working tree is not clean; commit or stash first, then re-run")
    if _git("tag", "--list", tag, capture=True):
        _die(f"tag {tag} already exists")
    if version == stamp_version.read_version():
        _die(f"version is already {version}; bump to a new version")

    branch = _git("rev-parse", "--abbrev-ref", "HEAD", capture=True)

    if dry_run:
        print(f"[dry-run] preconditions pass for {tag} on {branch}. Would:")
        print(f"  1. bump to {version} in __init__.py, pyproject.toml, desktop/package.json")
        print(f"  2. commit 'Release {tag}' and create tag {tag}")
        print(f"  3. push {branch} + {tag} to origin (triggers release.yml)")
        print("nothing was changed.")
        return 0

    # --- bump ---------------------------------------------------------------
    _replace_once(
        _INIT,
        r'^__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"',
        "__version__",
    )
    _replace_once(
        _PYPROJECT,
        r'^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        "version",
    )
    stamp_version.stamp()  # desktop/package.json
    print(f"bumped to {version}: __init__.py, pyproject.toml, desktop/package.json")

    # --- commit / tag / push ------------------------------------------------
    _git("add", "src/job_applier/__init__.py", "pyproject.toml", "desktop/package.json")
    _git("commit", "-m", f"Release {tag}")
    _git("tag", tag)
    print(f"committed and tagged {tag} on {branch}")

    _git("push", "origin", "HEAD")
    _git("push", "origin", tag)
    print(
        f"\npushed {branch} + {tag}. The release workflow is now building.\n"
        f"When it finishes, publish the DRAFT release at:\n"
        f"  https://github.com/hhagely/job-applier/releases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
