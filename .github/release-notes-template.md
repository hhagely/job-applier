<!--
Release-notes template (Phase 7). The release workflow creates a DRAFT release
for each v* tag with auto-generated commit notes; edit the draft with this
structure before publishing.

Cutting a release:
  1. Bump the single source of truth: src/job_applier/__init__.py __version__
     (and pyproject.toml version — the version-sources-agree test checks both).
  2. Commit, then tag:  git tag vX.Y.Z && git push origin vX.Y.Z
  3. The release workflow builds the Windows .exe + Linux AppImage/.deb on native
     runners, runs the data-isolation guard, and uploads to a draft release.
  4. Edit the draft (this template), then Publish.
-->

## job-applier vX.Y.Z

<!-- One or two sentences on the theme of this release. -->

### Highlights
- ...

### Fixes
- ...

### Install
Download the installer for your OS below. See the
[README Install section](https://github.com/hhagely/job-applier#install-desktop-app)
for the Windows SmartScreen "More info → Run anyway" step (the app is unsigned)
and the optional AI-CLI prerequisite.

- **Windows:** `job-applier-Setup-X.Y.Z.exe`
- **Linux:** `job-applier-X.Y.Z.AppImage` or `job-applier_X.Y.Z_amd64.deb`

### Notes
- Unsigned build; no background auto-update — the app links to this page when a
  newer release exists.
