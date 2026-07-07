import { api, type Job } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import type { PageServerLoad } from './$types';

function isArchived(j: Job): boolean {
	return j.application?.status === 'archived';
}

function isUnreviewed(j: Job): boolean {
	const s = j.application?.status;
	return s == null || s === 'new';
}

export const load: PageServerLoad = async ({ fetch }) => {
	const base = serverApiBase();
	const [allPassed, followups] = await Promise.all([
		api.listJobs(fetch, base, { filter_status: 'passed', limit: 500 }),
		api.getFollowups(fetch, base)
	]);

	const jobs = allPassed.filter((j) => !isArchived(j));
	const scored = jobs.filter((j) => j.score != null);

	const strong = scored.filter((j) => (j.score?.score ?? 0) >= 80).length;
	const applied = jobs.filter((j) => j.application?.status === 'applied').length;
	const rejected = jobs.filter((j) => j.application?.status === 'rejected').length;
	const unreviewed = jobs.filter(isUnreviewed).length;

	const avg =
		scored.length > 0
			? Math.round(scored.reduce((s, j) => s + (j.score?.score ?? 0), 0) / scored.length)
			: null;

	// Score distribution buckets.
	const dist = [
		{ label: '80–100', band: 'strong', n: scored.filter((j) => (j.score?.score ?? 0) >= 80).length },
		{
			label: '65–79',
			band: 'good',
			n: scored.filter((j) => (j.score?.score ?? 0) >= 65 && (j.score?.score ?? 0) < 80).length
		},
		{
			label: '< 65',
			band: 'weak',
			n: scored.filter((j) => (j.score?.score ?? 0) < 65).length
		}
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
		dist,
		bySource,
		topMatches,
		followups: followups.slice(0, 5)
	};
};
