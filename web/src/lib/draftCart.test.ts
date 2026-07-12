import { beforeEach, describe, expect, it, vi } from 'vitest';

// Force the browser-gated localStorage paths to run.
vi.mock('$app/environment', () => ({ browser: true }));

import { draftCart } from './draftCart.svelte';

const STORAGE_KEY = 'job-applier:draft-cart';

beforeEach(() => {
	localStorage.clear();
	draftCart.clear();
});

describe('draftCart store', () => {
	it('add / has / remove reflect membership and persist', () => {
		draftCart.add(1);
		draftCart.add(2);
		expect(draftCart.ids).toEqual([1, 2]);
		expect(draftCart.has(1)).toBe(true);
		expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual([1, 2]);

		draftCart.remove(1);
		expect(draftCart.ids).toEqual([2]);
		expect(draftCart.has(1)).toBe(false);
		expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual([2]);
	});

	it('add is idempotent and remove of a missing id is a no-op', () => {
		draftCart.add(5);
		draftCart.add(5);
		expect(draftCart.ids).toEqual([5]);
		draftCart.remove(99);
		expect(draftCart.ids).toEqual([5]);
	});

	it('toggle adds then removes', () => {
		draftCart.toggle(7);
		expect(draftCart.has(7)).toBe(true);
		draftCart.toggle(7);
		expect(draftCart.has(7)).toBe(false);
	});

	it('clear empties the cart and persists the empty state', () => {
		draftCart.add(1);
		draftCart.add(2);
		draftCart.clear();
		expect(draftCart.ids).toEqual([]);
		expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')).toEqual([]);
	});

	it('load() ignores malformed / non-array persisted JSON', async () => {
		vi.resetModules();
		localStorage.setItem(STORAGE_KEY, '{not json');
		const mod = await import('./draftCart.svelte');
		expect(mod.draftCart.ids).toEqual([]);

		vi.resetModules();
		localStorage.setItem(STORAGE_KEY, JSON.stringify({ nope: true }));
		const mod2 = await import('./draftCart.svelte');
		expect(mod2.draftCart.ids).toEqual([]);
	});

	it('load() keeps only integer ids from persisted JSON', async () => {
		vi.resetModules();
		localStorage.setItem(STORAGE_KEY, JSON.stringify([1, 'two', 3.5, 4]));
		const mod = await import('./draftCart.svelte');
		expect(mod.draftCart.ids).toEqual([1, 4]);
	});
});
