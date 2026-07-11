import { beforeEach, describe, expect, it, vi } from 'vitest';

// Force the browser-gated paths (localStorage + <html> attributes) to run.
vi.mock('$app/environment', () => ({ browser: true }));

import { appearance, initAppearance } from './appearance.svelte';

const ATTRS = [
	'data-accent',
	'data-reading-size',
	'data-reading-font',
	'data-reading-spacing',
	'data-reading-width'
];

beforeEach(() => {
	localStorage.clear();
	for (const a of ATTRS) document.documentElement.removeAttribute(a);
	appearance.reset();
});

describe('appearance store', () => {
	it('defaults to md / sans / normal / comfortable / blue', () => {
		expect(appearance.readingSize).toBe('md');
		expect(appearance.readingFont).toBe('sans');
		expect(appearance.readingSpacing).toBe('normal');
		expect(appearance.readingWidth).toBe('comfortable');
		expect(appearance.accent).toBe('blue');
	});

	it('set() updates getters, writes <html> data-* attributes, and persists', () => {
		appearance.set({ readingSize: 'lg', accent: 'violet', readingFont: 'serif' });

		expect(appearance.readingSize).toBe('lg');
		expect(appearance.readingFont).toBe('serif');

		const de = document.documentElement;
		expect(de.getAttribute('data-reading-size')).toBe('lg');
		expect(de.getAttribute('data-accent')).toBe('violet');
		expect(de.getAttribute('data-reading-font')).toBe('serif');

		const stored = JSON.parse(localStorage.getItem('ja-appearance') ?? '{}');
		expect(stored).toMatchObject({ readingSize: 'lg', accent: 'violet', readingFont: 'serif' });
	});

	it('initAppearance() re-applies stored prefs onto <html>', () => {
		appearance.set({ readingWidth: 'full' });
		document.documentElement.removeAttribute('data-reading-width');

		initAppearance();

		expect(document.documentElement.getAttribute('data-reading-width')).toBe('full');
	});

	it('reset() returns every field to its default', () => {
		appearance.set({ readingSize: 'xl', accent: 'rose', readingSpacing: 'relaxed' });
		appearance.reset();

		expect(appearance.readingSize).toBe('md');
		expect(appearance.accent).toBe('blue');
		expect(appearance.readingSpacing).toBe('normal');
	});

	it('coerces unknown persisted values back to defaults on load, keeping valid ones', async () => {
		vi.resetModules();
		localStorage.setItem(
			'ja-appearance',
			JSON.stringify({ readingSize: 'huge', accent: 'chartreuse', readingFont: 'serif' })
		);

		const mod = await import('./appearance.svelte');

		expect(mod.appearance.readingSize).toBe('md'); // invalid -> default
		expect(mod.appearance.accent).toBe('blue'); // invalid -> default
		expect(mod.appearance.readingFont).toBe('serif'); // valid -> kept
	});
});
