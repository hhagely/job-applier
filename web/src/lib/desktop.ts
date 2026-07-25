// Access to the Electron preload bridge (window.desktop), if present. In a
// plain browser (dev `make web`) this is all absent, so the titlebar hides its
// window controls and the app still works as a normal web page.

import { browser } from '$app/environment';

// Auto-update events streamed from the main process (electron-updater). Mirrors the
// payloads sent in desktop/main.js. `idle` is the pre-check resting state.
export type UpdaterEventType =
	| 'idle'
	| 'checking'
	| 'available'
	| 'not-available'
	| 'progress'
	| 'downloaded'
	| 'error';

/** Trimmed UpdateInfo the popover + Settings card render. */
export interface UpdateDetails {
	version?: string;
	releaseDate?: string;
	sizeBytes?: number;
	notes?: string[];
}

export interface UpdaterEvent {
	type: UpdaterEventType;
	/** Present on available / not-available / downloaded. */
	info?: UpdateDetails;
	/** 0–100 on progress. */
	percent?: number;
	/** Present on error. */
	message?: string;
}

export interface DesktopUpdater {
	/** Subscribe to update events; returns an unsubscribe fn. */
	onEvent: (cb: (e: UpdaterEvent) => void) => () => void;
	/** Last cached event (for a late mount). */
	getState: () => Promise<UpdaterEvent>;
	/** Re-run the update check. */
	check: () => Promise<UpdaterEvent>;
	/** Start downloading the available update (two-phase flow). */
	download: () => Promise<unknown>;
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
