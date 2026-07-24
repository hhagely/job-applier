// Client-side mirror of the Electron auto-update state (electron-updater runs in
// the main process; see desktop/main.js + preload.js). Subscribes once to the
// bridge's event stream and exposes the current state plus `check()` / `install()`
// actions. In a plain browser (no `window.desktop.updater`) it stays inert and the
// UpdateBanner falls back to the server-driven "Open Releases" link.

import { browser } from '$app/environment';
import { desktop, type UpdaterEvent } from './desktop';

export class Updater {
	event = $state<UpdaterEvent>({ state: 'idle' });

	constructor() {
		if (!browser) return;
		const bridge = desktop()?.updater;
		if (!bridge) return;

		// Live stream first, then backfill the cached state — but never let a stale
		// cached read clobber an event that already arrived (the check fires around
		// the same time the window mounts).
		bridge.onEvent((e) => {
			this.event = e;
		});
		bridge
			.getState()
			.then((e) => {
				if (this.event.state === 'idle' && e) this.event = e;
			})
			.catch(() => {});
	}

	/** Whether the Electron auto-update bridge is present (vs. a plain browser). */
	get available(): boolean {
		return browser && !!desktop()?.updater;
	}

	/** True while there's an in-progress or ready auto-update to surface. */
	get active(): boolean {
		return (
			this.event.state === 'available' ||
			this.event.state === 'downloading' ||
			this.event.state === 'downloaded'
		);
	}

	check(): void {
		desktop()?.updater?.check().catch(() => {});
	}

	install(): void {
		desktop()?.updater?.install();
	}
}

export const updater = new Updater();
