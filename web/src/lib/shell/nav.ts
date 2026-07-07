// Sidebar navigation model, shared with the command palette and the
// Cmd/Ctrl-1..6 shortcuts (order here IS the numeric order).

import type { ICONS } from '$lib/Icon.svelte';

export type CountKey = 'queue' | 'followups';

export interface NavItem {
	id: string;
	href: string;
	label: string;
	icon: keyof typeof ICONS;
	/** Which sidebar count badge to show, if any. */
	countKey?: CountKey;
	/** Section label rendered above this item in the sidebar. */
	group?: string;
	/** Extra pathnames (besides href) that should mark this item active. */
	matchPrefixes?: string[];
}

export const NAV: NavItem[] = [
	{ id: 'dashboard', href: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
	{ id: 'queue', href: '/', label: 'Queue', icon: 'queue', countKey: 'queue', matchPrefixes: ['/jobs'] },
	{ id: 'followups', href: '/followups', label: 'Follow-ups', icon: 'clock', countKey: 'followups' },
	{ id: 'resume', href: '/resume', label: 'Resume', icon: 'doc', group: 'Profile' },
	{ id: 'search', href: '/search', label: 'Search profile', icon: 'funnel' },
	{ id: 'settings', href: '/settings', label: 'Settings', icon: 'gear' }
];

/** Which nav item is active for a given pathname. */
export function activeNavId(pathname: string): string {
	// Longest, most-specific match first.
	for (const item of NAV) {
		if (item.href !== '/' && pathname.startsWith(item.href)) return item.id;
		for (const p of item.matchPrefixes ?? []) {
			if (pathname.startsWith(p)) return item.id;
		}
	}
	return pathname === '/' ? 'queue' : '';
}
