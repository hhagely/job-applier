// System-aware theme store. Preference is System / Light / Dark, persisted in
// localStorage under `ja-theme`; "System" tracks prefers-color-scheme live.
// The effective theme is written to <html data-theme> (the same attribute the
// pre-paint guard in app.html sets, so there is no flash on load).

import { browser } from '$app/environment';

export type ThemePref = 'system' | 'light' | 'dark';
export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'ja-theme';

function readPref(): ThemePref {
	if (!browser) return 'system';
	const raw = localStorage.getItem(STORAGE_KEY);
	return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system';
}

function systemIsLight(): boolean {
	return (
		browser &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-color-scheme: light)').matches
	);
}

function effective(p: ThemePref): Theme {
	if (p === 'system') return systemIsLight() ? 'light' : 'dark';
	return p;
}

let pref = $state<ThemePref>(readPref());
let current = $state<Theme>(effective(readPref()));

function apply() {
	current = effective(pref);
	if (browser) document.documentElement.setAttribute('data-theme', current);
}

export const theme = {
	get pref(): ThemePref {
		return pref;
	},
	get effective(): Theme {
		return current;
	},
	set(next: ThemePref) {
		pref = next;
		if (browser) localStorage.setItem(STORAGE_KEY, next);
		apply();
	},
	/** Quick toggle for the titlebar: flip the *effective* theme and pin it. */
	toggle() {
		this.set(current === 'light' ? 'dark' : 'light');
	}
};

/**
 * Attach the live prefers-color-scheme listener. Call once from the root
 * layout's onMount; returns a teardown. Re-applies on mount so SSR/first-paint
 * and the store agree.
 */
export function initTheme(): () => void {
	if (!browser) return () => {};
	apply();
	const mq = window.matchMedia('(prefers-color-scheme: light)');
	const onChange = () => {
		if (pref === 'system') apply();
	};
	mq.addEventListener('change', onChange);
	return () => mq.removeEventListener('change', onChange);
}
