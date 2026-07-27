# Frontend (SvelteKit)

Loaded when working under [web/](.). Moved out of the root `CLAUDE.md` so it isn't resident in every session.

The user is learning SvelteKit on this project — prefer idiomatic SvelteKit patterns (form actions, `+page.server.ts` loaders) over client-side fetch. **Status changes go through form actions**, not client `fetch`; keep new mutations server-side in `+page.server.ts`.

## Design system

[src/app.css](src/app.css) is the desktop-redesign design system (Phase 8): system-aware OKLch tokens for **both** light + dark (driven by `prefers-color-scheme` + a persisted `ja-theme` override; pre-paint guard in [src/app.html](src/app.html)), plus shared primitives (`.card`, `.btn`, `.pill`, `.tag`, `.score`, `.input`). Old token names (`--panel`, `--ok`, `--warn`, `--bad`) are back-compat aliases onto the new palette. Prefer tokens over hardcoded hex so both themes stay styled.

## Shell + layout

The desktop shell (titlebar + sidebar + status bar + Cmd/Ctrl-K command palette + `?` shortcuts + keyboard nav) lives in [src/lib/shell/](src/lib/shell/) and is composed by [src/routes/+layout.svelte](src/routes/+layout.svelte); the sidebar count badges come from [src/routes/+layout.server.ts](src/routes/+layout.server.ts).

Match-score band thresholds (green ≥80 / amber 65–79 / rose <65) are centralized in [src/lib/score.ts](src/lib/score.ts) + `ScoreBadge`.

`/` is the Queue (master–detail: list + read-only match-breakdown pane; full mutations still on `/jobs/[id]`); `/dashboard` is the landing view.

Electron draws a frameless window and the SvelteKit titlebar provides the window controls (IPC in [../desktop/main.js](../desktop/main.js) / [../desktop/preload.js](../desktop/preload.js)); the controls only render when the `window.desktop` bridge is present.

## Notes

- [src/lib/api.ts](src/lib/api.ts) must stay browser-safe — no `$env/dynamic/private` imports.
- [src/lib/draftCart.svelte.ts](src/lib/draftCart.svelte.ts) is the rune-based store for the cross-route draft cart, used from `/`, `/jobs/[id]`, and `/followups`.
