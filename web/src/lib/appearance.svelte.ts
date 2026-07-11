// User appearance preferences beyond light/dark (which lives in theme.svelte.ts).
// Covers reading-surface typography (job descriptions, draft preview, rationale)
// and the app accent hue. Persisted as one JSON blob under `ja-appearance` and
// applied to <html> as data-* attributes; app.css derives CSS variables from
// them. The pre-paint guard in app.html mirrors this apply step to avoid a flash.

import { browser } from '$app/environment';

export type ReadingSize = 'sm' | 'md' | 'lg' | 'xl';
export type ReadingFont = 'sans' | 'serif';
export type ReadingSpacing = 'normal' | 'relaxed';
export type ReadingWidth = 'comfortable' | 'full';
export type Accent = 'blue' | 'violet' | 'teal' | 'amber' | 'rose';

export type Appearance = {
	readingSize: ReadingSize;
	readingFont: ReadingFont;
	readingSpacing: ReadingSpacing;
	readingWidth: ReadingWidth;
	accent: Accent;
};

const STORAGE_KEY = 'ja-appearance';

const DEFAULTS: Appearance = {
	readingSize: 'md',
	readingFont: 'sans',
	readingSpacing: 'normal',
	readingWidth: 'comfortable',
	accent: 'blue'
};

const SIZES: readonly ReadingSize[] = ['sm', 'md', 'lg', 'xl'];
const FONTS: readonly ReadingFont[] = ['sans', 'serif'];
const SPACINGS: readonly ReadingSpacing[] = ['normal', 'relaxed'];
const WIDTHS: readonly ReadingWidth[] = ['comfortable', 'full'];
const ACCENTS: readonly Accent[] = ['blue', 'violet', 'teal', 'amber', 'rose'];

function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
	return typeof value === 'string' && (allowed as readonly string[]).includes(value)
		? (value as T)
		: fallback;
}

function read(): Appearance {
	if (!browser) return { ...DEFAULTS };
	let raw: Record<string, unknown> = {};
	try {
		raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') ?? {};
	} catch {
		raw = {};
	}
	return {
		readingSize: pick(raw.readingSize, SIZES, DEFAULTS.readingSize),
		readingFont: pick(raw.readingFont, FONTS, DEFAULTS.readingFont),
		readingSpacing: pick(raw.readingSpacing, SPACINGS, DEFAULTS.readingSpacing),
		readingWidth: pick(raw.readingWidth, WIDTHS, DEFAULTS.readingWidth),
		accent: pick(raw.accent, ACCENTS, DEFAULTS.accent)
	};
}

let prefs = $state<Appearance>(read());

function apply() {
	if (!browser) return;
	const de = document.documentElement;
	de.setAttribute('data-reading-size', prefs.readingSize);
	de.setAttribute('data-reading-font', prefs.readingFont);
	de.setAttribute('data-reading-spacing', prefs.readingSpacing);
	de.setAttribute('data-reading-width', prefs.readingWidth);
	de.setAttribute('data-accent', prefs.accent);
}

export const appearance = {
	get readingSize(): ReadingSize {
		return prefs.readingSize;
	},
	get readingFont(): ReadingFont {
		return prefs.readingFont;
	},
	get readingSpacing(): ReadingSpacing {
		return prefs.readingSpacing;
	},
	get readingWidth(): ReadingWidth {
		return prefs.readingWidth;
	},
	get accent(): Accent {
		return prefs.accent;
	},
	set(patch: Partial<Appearance>) {
		prefs = { ...prefs, ...patch };
		if (browser) localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
		apply();
	},
	reset() {
		this.set({ ...DEFAULTS });
	}
};

/**
 * Sync <html> attributes with the stored prefs. Call once from the root layout's
 * onMount; the app.html guard already did this pre-paint, so this is a no-op
 * reconcile that also covers non-Electron SSR / hydration.
 */
export function initAppearance(): void {
	apply();
}
