import { afterEach, describe, expect, it, vi } from 'vitest';

import { Updater } from './updater.svelte';

// A controllable stand-in for the electron-updater preload bridge.
function fakeBridge() {
	let handler: ((e: unknown) => void) | null = null;
	let resolveState!: (e: unknown) => void;
	const statePromise = new Promise<unknown>((res) => (resolveState = res));
	return {
		onEvent: (cb: (e: unknown) => void) => {
			handler = cb;
			return () => (handler = null);
		},
		getState: () => statePromise,
		check: vi.fn(async () => ({ state: 'idle' })),
		install: vi.fn(),
		// test helpers
		emit: (e: unknown) => handler?.(e),
		resolveState: (e: unknown) => resolveState(e)
	};
}

function installBridge(bridge: unknown) {
	(window as unknown as { desktop?: unknown }).desktop = { updater: bridge };
}

afterEach(() => {
	delete (window as unknown as { desktop?: unknown }).desktop;
});

describe('Updater store', () => {
	it('stays idle and unavailable with no desktop bridge', () => {
		const u = new Updater();
		expect(u.event.state).toBe('idle');
		expect(u.available).toBe(false);
		expect(u.active).toBe(false);
	});

	it('applies streamed events and reports active during a download', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.emit({ state: 'downloading', percent: 40 });
		expect(u.event).toEqual({ state: 'downloading', percent: 40 });
		expect(u.available).toBe(true);
		expect(u.active).toBe(true);
	});

	it('backfills cached state only while still idle (never clobbers a live event)', async () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		// A live "downloaded" arrives before the cached getState() resolves...
		bridge.emit({ state: 'downloaded', version: 'v9.9.9' });
		bridge.resolveState({ state: 'checking' }); // stale cache
		await Promise.resolve();
		await Promise.resolve();
		expect(u.event.state).toBe('downloaded'); // not overwritten by the stale read
	});

	it('backfills the cached state when no live event has arrived', async () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.resolveState({ state: 'available', version: 'v1.2.3' });
		await Promise.resolve();
		await Promise.resolve();
		expect(u.event).toEqual({ state: 'available', version: 'v1.2.3' });
	});

	it('proxies install() and check() to the bridge', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		u.install();
		expect(bridge.install).toHaveBeenCalled();
		u.check();
		expect(bridge.check).toHaveBeenCalled();
	});
});
