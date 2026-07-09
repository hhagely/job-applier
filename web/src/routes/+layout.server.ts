import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { activeJobs, isUnreviewed } from '$lib/jobFilters';
import { scoreBand } from '$lib/score';
import type { LayoutServerLoad } from './$types';

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
export const load: LayoutServerLoad = async ({ fetch }) => {
	const base = serverApiBase();

	let aiProvider: string | null = null;
	try {
		aiProvider = (await api.getSelectedProvider(fetch, base)).selected;
	} catch {
		aiProvider = null;
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

	return { apiBase: base, aiProvider, counts };
};
