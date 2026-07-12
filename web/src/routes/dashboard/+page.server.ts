import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { activeJobs, isUnreviewed } from '$lib/jobFilters';
import { scoreBand } from '$lib/score';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const base = serverApiBase();
	const [allPassed, followups] = await Promise.all([
		api.listJobs(fetch, base, { filter_status: 'passed', limit: 500 }),
		api.getFollowups(fetch, base)
	]);

	const jobs = activeJobs(allPassed);
	const scored = jobs.filter((j) => j.score != null);
	// Unscored or stale-scored roles — the count the "Score pending" action targets.
	const pending = jobs.filter((j) => j.score == null || j.score?.is_stale).length;

	const strong = scored.filter((j) => scoreBand(j.score?.score) === 'strong').length;
	const applied = jobs.filter((j) => j.application?.status === 'applied').length;
	const rejected = jobs.filter((j) => j.application?.status === 'rejected').length;
	const unreviewed = jobs.filter(isUnreviewed).length;

	const avg =
		scored.length > 0
			? Math.round(scored.reduce((s, j) => s + (j.score?.score ?? 0), 0) / scored.length)
			: null;

	// Score distribution buckets.
	const dist = [
		{ label: '80–100', band: 'strong', n: scored.filter((j) => scoreBand(j.score?.score) === 'strong').length },
		{ label: '65–79', band: 'good', n: scored.filter((j) => scoreBand(j.score?.score) === 'good').length },
		{ label: '< 65', band: 'weak', n: scored.filter((j) => scoreBand(j.score?.score) === 'weak').length }
	];

	// By-source counts (descending).
	const bySourceMap = new Map<string, number>();
	for (const j of jobs) bySourceMap.set(j.source, (bySourceMap.get(j.source) ?? 0) + 1);
	const bySource = [...bySourceMap.entries()]
		.map(([source, n]) => ({ source, n }))
		.sort((a, b) => b.n - a.n);

	// Top unreviewed matches by score.
	const topMatches = scored
		.filter(isUnreviewed)
		.sort((a, b) => (b.score?.score ?? 0) - (a.score?.score ?? 0))
		.slice(0, 6);

	return {
		kpis: {
			jobs: jobs.length,
			scored: scored.length,
			unreviewed,
			strong,
			applied,
			rejected,
			followupsDue: followups.length,
			avg
		},
		pending,
		dist,
		bySource,
		topMatches,
		followups: followups.slice(0, 5)
	};
};

export const actions: Actions = {
	// Kick off a background scoring run. The mutation stays server-side per
	// convention; the client then polls GET /api/ai/tasks/{id} for progress.
	scorePending: async ({ fetch }) => {
		try {
			const { task_id } = await api.startScorePending(fetch, serverApiBase(), {
				include_stale: true
			});
			return { ok: true, task_id };
		} catch (e) {
			// The API returns 409 when no provider is selected / no active resume.
			return fail(409, { error: (e as Error).message });
		}
	},

	// Kick off a background scrape of every source (needs no AI provider).
	runIngest: async ({ fetch }) => {
		try {
			const { task_id } = await api.startIngest(fetch, serverApiBase());
			return { ok: true, task_id, kind: 'ingest' };
		} catch (e) {
			return fail(500, { error: (e as Error).message });
		}
	}
};
