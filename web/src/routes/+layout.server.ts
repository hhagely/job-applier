import { redirect } from '@sveltejs/kit';
import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { activeJobs, isUnreviewed } from '$lib/jobFilters';
import { scoreBand } from '$lib/score';
import type { LayoutServerLoad } from './$types';

// Set by the onboarding wizard's "Skip / Finish" so a resume-less user isn't
// re-trapped in the guided flow on every navigation (the redirect is not a hard gate).
const ONBOARDING_DISMISSED_COOKIE = 'ja_onboarding_dismissed';

export interface ShellCounts {
	/** Total non-archived jobs on the passed queue. */
	jobs: number | null;
	/** Unreviewed (no application, or status "new"). Shown on the Queue nav. */
	queue: number | null;
	/** Follow-ups currently due. Shown on the Follow-ups nav (red). */
	followups: number | null;
	/** Strong matches (score >= 80) — used by the dashboard/statusbar. */
	strong: number | null;
}

// Expose the browser-reachable API base (injected as window.__API_BASE__ by the
// layout for browser-only PDF links), the selected AI provider for the shell
// indicator, and cheap counts for the sidebar/status-bar badges. Every piece is
// defensive: a failure degrades to null rather than breaking navigation.
export const load: LayoutServerLoad = async ({ fetch, url, cookies }) => {
	const base = serverApiBase();

	// First-run signal: no active resume means the app can't score or draft, so a
	// stranger who just installed it is walked through the guided /onboarding flow.
	// Fail OPEN (treat as not-needed) on any error so a flaky check never traps the
	// user in onboarding. Honor the dismissed cookie so "Skip" sticks.
	if (url.pathname !== '/onboarding' && !cookies.get(ONBOARDING_DISMISSED_COOKIE)) {
		let hasResume: boolean | null = null;
		try {
			hasResume = !!(await api.getCurrentResume(fetch, base));
		} catch {
			hasResume = null; // fail open — never trap the user on a flaky check
		}
		// redirect() throws, so it must live OUTSIDE the try above (a catch-all
		// would otherwise swallow the redirect signal).
		if (hasResume === false) {
			redirect(307, '/onboarding');
		}
		if (hasResume === true) {
			// Established install: stop re-checking on every navigation.
			cookies.set(ONBOARDING_DISMISSED_COOKIE, '1', {
				path: '/',
				maxAge: 60 * 60 * 24 * 365,
				httpOnly: false,
				sameSite: 'lax'
			});
		}
	}

	let aiProvider: string | null = null;
	try {
		aiProvider = (await api.getSelectedProvider(fetch, base)).selected;
	} catch {
		aiProvider = null;
	}

	// In-app update check (cached + fail-soft server-side). A null result just
	// hides the banner; it never blocks the page.
	let update = null;
	try {
		update = await api.getUpdate(fetch, base);
	} catch {
		update = null;
	}

	const counts: ShellCounts = { jobs: null, queue: null, followups: null, strong: null };
	try {
		const [passed, followups] = await Promise.all([
			api.listJobs(fetch, base, { filter_status: 'passed', limit: 500 }),
			api.getFollowups(fetch, base)
		]);
		const active = activeJobs(passed);
		counts.jobs = active.length;
		counts.queue = active.filter(isUnreviewed).length;
		counts.strong = active.filter((j) => scoreBand(j.score?.score) === 'strong').length;
		counts.followups = followups.length;
	} catch {
		// leave counts as nulls — badges simply won't render
	}

	return { apiBase: base, aiProvider, counts, update };
};
