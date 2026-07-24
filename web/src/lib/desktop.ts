// Access to the Electron preload bridge (window.desktop), if present. In a
// plain browser (dev `make web`) this is all absent, so the titlebar hides its
// window controls and the app still works as a normal web page.

import { browser } from '$app/environment';

// Auto-update state streamed from the main process (electron-updater). Mirrors the
// payloads pushed in desktop/main.js. `idle`/`none` mean "nothing to show".
export type UpdaterState =
	| 'idle'
	| 'checking'
	| 'available'
	| 'downloading'
	| 'downloaded'
	| 'none'
	| 'error';

export interface UpdaterEvent {
	state: UpdaterState;
	/** Target version (available/downloaded) or the up-to-date version (none). */
	version?: string;
	/** 0–100 while downloading. */
	percent?: number;
	/** Set only when state === 'error'. */
	error?: string;
}

export interface DesktopUpdater {
	/** Subscribe to state transitions; returns an unsubscribe fn. */
	onEvent: (cb: (e: UpdaterEvent) => void) => () => void;
	/** Current cached state (for a late mount). */
	getState: () => Promise<UpdaterEvent>;
	/** Re-run the update check. */
	check: () => Promise<UpdaterEvent>;
	/** Quit and install a downloaded update. */
	install: () => void;
}

export interface DesktopBridge {
	isElectron?: boolean;
	version?: string;
	platform?: string;
	windowControls?: {
		minimize: () => void;
		maximize: () => void;
		close: () => void;
	};
	updater?: DesktopUpdater;
}

export function desktop(): DesktopBridge | null {
	if (!browser) return null;
	return (window as unknown as { desktop?: DesktopBridge }).desktop ?? null;
}

export function isElectron(): boolean {
	return Boolean(desktop()?.isElectron);
}
