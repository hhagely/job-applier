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
		check: vi.fn(async () => ({ type: 'idle' })),
		download: vi.fn(async () => undefined),
		install: vi.fn(),
		// test helpers
		emit: (e: unknown) => handler?.(e),
		resolveState: (e: unknown) => resolveState(e)
	};
}

function installBridge(bridge: unknown, version = '2.0.0') {
	(window as unknown as { desktop?: unknown }).desktop = { version, updater: bridge };
}

afterEach(() => {
	delete (window as unknown as { desktop?: unknown }).desktop;
});

describe('Updater store', () => {
	it('is inert with no desktop bridge', () => {
		const u = new Updater();
		expect(u.present).toBe(false);
		expect(u.available).toBe(false);
	});

	it('surfaces an available update with version, size, date and notes', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.emit({
			type: 'available',
			info: { version: '2.1.0', sizeBytes: 50 * 1048576, releaseDate: '2026-07-22', notes: ['A', 'B'] }
		});
		expect(u.available).toBe(true);
		expect(u.version).toBe('2.1.0');
		expect(u.size).toBe('50.0 MB');
		expect(u.notes).toEqual(['A', 'B']);
		expect(u.checkedLabel).toBe('just now');
		expect(u.present).toBe(true);
		expect(u.currentVersion).toBe('2.0.0');
	});

	it('tracks download progress then the downloaded state', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.emit({ type: 'available', info: { version: '2.1.0' } });
		bridge.emit({ type: 'progress', percent: 41.6 });
		expect(u.downloading).toBe(true);
		expect(u.percent).toBe(42);
		bridge.emit({ type: 'downloaded', info: { version: '2.1.0' } });
		expect(u.downloaded).toBe(true);
		expect(u.downloading).toBe(false);
	});

	it('clears availability on not-available', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.emit({ type: 'available', info: { version: '2.1.0' } });
		bridge.emit({ type: 'not-available', info: { version: '2.0.0' } });
		expect(u.available).toBe(false);
	});

	it('install() downloads first, then restarts once downloaded', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.emit({ type: 'available', info: { version: '2.1.0' } });

		u.install(); // first tap → download
		expect(bridge.download).toHaveBeenCalledTimes(1);
		expect(bridge.install).not.toHaveBeenCalled();
		expect(u.downloading).toBe(true);

		bridge.emit({ type: 'downloaded', info: { version: '2.1.0' } });
		u.install(); // second tap → restart
		expect(bridge.install).toHaveBeenCalledTimes(1);
	});

	it('check() re-runs the check and marks it checking', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		u.check();
		expect(bridge.check).toHaveBeenCalled();
		expect(u.checkedLabel).toBe('checking…');
	});

	it('toggles the popover', () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		expect(u.popoverOpen).toBe(false);
		u.togglePopover();
		expect(u.popoverOpen).toBe(true);
		u.closePopover();
		expect(u.popoverOpen).toBe(false);
	});

	it('backfills cached state only while no live event has arrived', async () => {
		const bridge = fakeBridge();
		installBridge(bridge);
		const u = new Updater();
		bridge.emit({ type: 'downloaded', info: { version: '9.9.9' } }); // live
		bridge.resolveState({ type: 'available', info: { version: '1.0.0' } }); // stale cache
		await Promise.resolve();
		await Promise.resolve();
		expect(u.downloaded).toBe(true);
		expect(u.version).toBe('9.9.9'); // not overwritten by the stale read
	});
});
