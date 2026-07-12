#!/usr/bin/env python3
"""Cut a release (Phase 7): bump the version on a PR branch, then tag main.

``main`` is protected (a repository ruleset requires every change to land through a
pull request), so a release is TWO phases instead of one direct push:

    make release VERSION=0.2.0        # phase 1: bump on release/v0.2.0 + open a PR
    # ... review + merge that PR on GitHub ...
    make release-tag VERSION=0.2.0    # phase 2: tag the merged commit + push the tag

Pushing the ``v*`` tag is what triggers ``.github/workflows/release.yml`` (per-OS
build -> data-isolation guard -> draft GitHub Release). The tag is pushed from your
machine, so the workflow fires as designed; the version bump reaches ``main`` the
only way the ruleset allows (a PR), not via a rejected ``git push origin main``.

Add ``DRY_RUN=1`` (``--dry-run``) to either phase to run every read-only check and
print the plan without branching, committing, pushing, tagging, or opening anything.

phase 1 (prepare):
  1. validate the version + a clean working tree,
  2. branch ``release/vX.Y.Z`` off the latest ``origin/main``,
  3. rewrite __version__ (src/job_applier/__init__.py) + version (pyproject.toml) +
     uv.lock (the job-applier self-package) + stamp desktop/package.json, and commit
     "Release vX.Y.Z",
  4. push the branch and open a PR into ``main``.

phase 2 (tag):
  1. fetch origin and verify ``origin/main`` is now at vX.Y.Z (i.e. the PR merged),
  2. verify the tag does not exist yet,
  3. tag ``origin/main``'s commit ``vX.Y.Z`` and push the tag.

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
_INIT_REL = "src/job_applier/__init__.py"
_INIT = _REPO_ROOT / _INIT_REL
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_UV_LOCK = _REPO_ROOT / "uv.lock"
_DEFAULT_BRANCH = "main"

# X.Y.Z with an optional pre-release suffix (e.g. 0.2.0-rc1).
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
_VERSION_ASSIGN_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


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


def _gh(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=capture,
        check=True,
    )
    return (result.stdout or "").strip() if capture else ""


def _ref_exists(ref: str) -> bool:
    """True if a local ref (branch/tag/commit) resolves."""
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _remote_has(kind: str, name: str) -> bool:
    """True if origin has a matching branch (kind='--heads') or tag ('--tags')."""
    return bool(_git("ls-remote", kind, "origin", name, capture=True))


def _version_at(ref: str) -> str:
    """Parse __version__ from __init__.py as it exists at *ref* (e.g. origin/main)."""
    m = _VERSION_ASSIGN_RE.search(_git("show", f"{ref}:{_INIT_REL}", capture=True))
    if not m:
        _die(f"could not find __version__ at {ref}")
    return m.group(1)


def _repo_slug() -> str | None:
    """owner/repo parsed from origin's URL (ssh or https), or None."""
    url = _git("remote", "get-url", "origin", capture=True)
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def _replace_once(path: Path, pattern: str, repl: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if count == 0:
        _die(f"could not find {what} in {path}")
    path.write_text(new_text, encoding="utf-8")


def _parse(argv: list[str], subcmd: str) -> tuple[str, bool]:
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1:
        _die(f"usage: release.py {subcmd} X.Y.Z [--dry-run]")
    version = args[0].lstrip("vV")
    if not _VERSION_RE.match(version):
        _die(f"'{version}' is not a valid version (expected X.Y.Z[-suffix])")
    return version, dry_run


def prepare(argv: list[str]) -> int:
    """Phase 1: bump the version on a release/ branch and open a PR into main."""
    version, dry_run = _parse(argv, "prepare")
    tag = f"v{version}"
    branch = f"release/{tag}"
    current = stamp_version.read_version()

    # --- preconditions (all read-only) --------------------------------------
    if version == current:
        _die(f"version is already {version}; bump to a new version")
    if _git("status", "--porcelain", capture=True):
        _die("working tree is not clean; commit or stash first, then re-run")
    if _remote_has("--tags", tag):
        _die(f"tag {tag} already exists on origin (already released?)")
    if _remote_has("--heads", branch) or _ref_exists(f"refs/heads/{branch}"):
        _die(f"branch {branch} already exists; delete it or pick a new version")

    if dry_run:
        print(f"[dry-run] would prepare release {tag} (current version: {current}). Would:")
        print(f"  1. branch {branch} off origin/{_DEFAULT_BRANCH}")
        print(f"  2. bump to {version} in __init__.py, pyproject.toml, uv.lock, desktop/package.json")
        print(f"  3. commit 'Release {tag}', push {branch}, open a PR into {_DEFAULT_BRANCH}")
        print(f"then, after the PR merges: make release-tag VERSION={version}")
        print("nothing was changed.")
        return 0

    # --- branch off the latest origin/main + bump ---------------------------
    start = _git("rev-parse", "--abbrev-ref", "HEAD", capture=True)
    _git("fetch", "origin", _DEFAULT_BRANCH)
    _git("checkout", "-b", branch, f"origin/{_DEFAULT_BRANCH}")

    # Once we've switched branches, any failure must restore the caller's branch
    # instead of stranding them on the half-built release/ branch.
    try:
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
        # Keep uv.lock in lockstep: job-applier is an editable/self package inside
        # its own lockfile, so a bump that skips uv.lock leaves the manifest and the
        # lock inconsistent -- and the next `uv run` (e.g. this very script) would
        # auto-sync + rewrite uv.lock, dirtying the tree and failing the clean check.
        _replace_once(
            _UV_LOCK,
            r'^name = "job-applier"\nversion = "[^"]*"',
            f'name = "job-applier"\nversion = "{version}"',
            'job-applier version in uv.lock',
        )
        stamp_version.stamp()  # desktop/package.json
        _git("add", _INIT_REL, "pyproject.toml", "uv.lock", "desktop/package.json")
        _git("commit", "-m", f"Release {tag}")
        _git("push", "-u", "origin", branch)
        print(f"pushed {branch} with the {version} bump")

        # --- open the PR (best-effort: the branch is already safe on origin) -----
        body = (
            f"Version bump to **{version}** "
            "(`__init__.py`, `pyproject.toml`, `uv.lock`, `desktop/package.json`).\n\n"
            f"After this merges, tag the release: `make release-tag VERSION={version}`."
        )
        try:
            url = _gh(
                "pr", "create",
                "--base", _DEFAULT_BRANCH,
                "--head", branch,
                "--title", f"Release {tag}",
                "--body", body,
                capture=True,
            )
            print(f"opened PR: {url}")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            slug = _repo_slug()
            where = (
                f"https://github.com/{slug}/compare/{_DEFAULT_BRANCH}...{branch}?expand=1"
                if slug
                else f"a PR from {branch} into {_DEFAULT_BRANCH}"
            )
            print(f"warning: could not open the PR automatically ({exc}); open it manually:\n  {where}")
    except BaseException:
        # Best-effort: don't leave the user on the release branch. The error
        # (and its one-line reason via __main__) still surfaces after this.
        subprocess.run(
            ["git", "checkout", start], cwd=_REPO_ROOT, capture_output=True, text=True
        )
        raise

    _git("checkout", start)  # the bump is safe on the pushed branch; return to where we were
    print(
        f"\nprepared {tag}. Merge the PR, then run:\n"
        f"  make release-tag VERSION={version}"
    )
    return 0


def tag_release(argv: list[str]) -> int:
    """Phase 2: verify the bump merged into main, then tag it and push the tag."""
    version, dry_run = _parse(argv, "tag")
    tag = f"v{version}"

    _git("fetch", "origin", _DEFAULT_BRANCH, "--tags")
    if _remote_has("--tags", tag):
        _die(f"tag {tag} already exists on origin (already released?)")
    if _ref_exists(f"refs/tags/{tag}"):
        _die(f"local tag {tag} already exists; remove it with: git tag -d {tag}")

    main_version = _version_at(f"origin/{_DEFAULT_BRANCH}")
    if main_version != version:
        _die(
            f"origin/{_DEFAULT_BRANCH} is at version {main_version}, not {version}; "
            f"merge the release PR first (make release VERSION={version})"
        )
    sha = _git("rev-parse", f"origin/{_DEFAULT_BRANCH}", capture=True)

    if dry_run:
        print(f"[dry-run] origin/{_DEFAULT_BRANCH} is at {version} ({sha[:9]}). Would:")
        print(f"  tag {sha[:9]} as {tag} and push it to origin (triggers release.yml)")
        print("nothing was changed.")
        return 0

    _git("tag", tag, sha)
    _git("push", "origin", tag)
    slug = _repo_slug()
    releases = f"https://github.com/{slug}/releases" if slug else "the Releases page"
    print(
        f"pushed {tag}. The release workflow is now building.\n"
        f"When it finishes, publish the DRAFT release at:\n  {releases}"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        _die("usage: release.py {prepare|tag} X.Y.Z [--dry-run]")
    sub, rest = argv[1], argv[2:]
    if sub == "prepare":
        return prepare(rest)
    if sub == "tag":
        return tag_release(rest)
    _die(f"unknown subcommand '{sub}' (expected 'prepare' or 'tag')")
    return 2  # unreachable; _die raises


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except subprocess.CalledProcessError as exc:
        # A git/gh command failed; show a one-line reason, not a Python traceback.
        _die(f"command failed (exit {exc.returncode}): {' '.join(map(str, exc.cmd))}")
