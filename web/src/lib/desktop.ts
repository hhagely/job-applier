// Access to the Electron preload bridge (window.desktop), if present. In a
// plain browser (dev `make web`) this is all absent, so the titlebar hides its
// window controls and the app still works as a normal web page.

import { browser } from '$app/environment';

export interface DesktopBridge {
	isElectron?: boolean;
	version?: string;
	platform?: string;
	windowControls?: {
		minimize: () => void;
		maximize: () => void;
		close: () => void;
	};
}

export function desktop(): DesktopBridge | null {
	if (!browser) return null;
	return (window as unknown as { desktop?: DesktopBridge }).desktop ?? null;
}

export function isElectron(): boolean {
	return Boolean(desktop()?.isElectron);
}
