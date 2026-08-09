# Desktop app (Electron)

Loaded when working under [desktop/](.). Moved out of the root `CLAUDE.md` so it isn't resident in every session.

## Build + dev

The desktop app is being built on the long-lived `desktop-app` branch, one PR per phase; specs live in [plans/desktop-app/](../plans/desktop-app/).

`make electron` runs the dev shell (Phase 6) — it builds the web bundle and serves it in-process, so a UI tweak means a full rebuild + restart. For active development use `make electron-dev` instead: it runs the backend (`uvicorn --reload`) + the Vite dev server + Electron under `electronmon` concurrently, so Svelte/CSS edits hot-reload in place and `main.js`/`preload.js` edits auto-restart Electron. That path keys off `main.js` reading `JOB_APPLIER_DEV_URL` (point the window at Vite) + `JOB_APPLIER_API_BASE` (reuse the external backend instead of spawning its own).

External `http(s)` links open in the OS browser via `shell.openExternal` (a window-open + `will-navigate` handler in `main.js`); internal `127.0.0.1`/`localhost` URLs stay in-app.

## Electron version

Pinned to **Electron 43** (`^43.3.0` — Chromium 150 / Node 24). It shipped on 33 (Chromium 130 / Node 20), which was already ~20 months and nine majors stale the day Phase 6 wrote it and long past upstream EOL; 43 clears 33 CVEs, several of them highs that matter here because the renderer displays untrusted scraped JD text (context-isolation bypass, cross-origin reads via custom protocols, ASAR integrity bypass).

Two things that make this a *verify-don't-assume* upgrade, both re-checked on 43:

- **The print-session exfil guard.** Electron 35 changed `WebRequestFilter` semantics; the guard survives only because `main.js` passes **no** filter. See the comment on `PRINT_PARTITION` before touching it.
- **PDF page geometry.** Electron 42 changed the offscreen-rendering device scale factor to 1.0. `printToPDF` with `preferCSSPageSize` is unaffected — output measures 612x792pt (Letter), matching the `@page` rules in [drafts.py](../src/job_applier/drafts.py).

`electron-builder` 25.1.8 packages Electron 43 with no changes, so that bump is independent (it's wanted separately for its own CVEs).

**Gotcha when scripting Electron directly:** this repo is often driven from inside a VS Code-hosted terminal, which exports `ELECTRON_RUN_AS_NODE=1`. That makes `require('electron')` return the binary path string instead of the API object (`app` is `undefined`). Unset it for the launch: `env -u ELECTRON_RUN_AS_NODE ...`. `make electron` from a normal shell is unaffected.

## Auto-update (electron-updater)

The packaged app checks GitHub Releases on launch and, when one exists, shows an **Update** pill in the titlebar that opens a popover (what's-new + a two-phase **Download & install** → **Restart to install**); the same controls live in **Settings → About & updates** and a ⌘/Ctrl-K "Check for updates" command. `autoDownload` is **off** — the download is user-initiated (`downloadUpdate` then `quitAndInstall`). It's **packaged-only** (guarded on `app.isPackaged` — a no-op in `make electron`/dev), fail-soft (feed errors surface a toast and recover), and unsigned (same one-time SmartScreen). Both Linux targets self-update (the `.deb` via `dpkg` behind a polkit prompt); only a box with neither `dpkg` nor `apt` errors.

Main-process electron-updater streams typed events over IPC (`updater:event`) into the [updater store](../web/src/lib/updater.svelte.ts) — the single source of truth for the [pill](../web/src/lib/shell/UpdatePill.svelte) + [popover](../web/src/lib/shell/UpdatePopover.svelte) + Settings card, whose DOM ids/classes mirror the Open Design prototype exactly.

**Release gotchas:**

- The release workflow must upload `latest*.yml` (the update feed) or the check finds nothing; `*.blockmap` only enables differential downloads.
- `nsis.artifactName` must stay space-free — GitHub rewrites spaces to dots on upload while electron-updater rewrites them to hyphens, so the default `${productName} Setup ${version}.${ext}` makes every Windows download 404.

This **replaces** the earlier "no background auto-update (needs code signing)" decision. The server-side [updates.py](../src/job_applier/updates.py) `/api/update` check is retained as an API but is no longer wired into the UI.

## Redesign source

The desktop **redesign** (Phase 8) source is in Open Design (Windows), project id `b0919236-9bed-4b67-bcad-e57c0d35b867`; reference copies are in [plans/desktop-app/redesign/](../plans/desktop-app/redesign/) — from WSL, re-pull via `"/mnt/c/Users/Herb/AppData/Roaming/Open Design/namespaces/release-stable-win/data/projects/b0919236-9bed-4b67-bcad-e57c0d35b867"` (see `redesign/HANDOFF.md`).
