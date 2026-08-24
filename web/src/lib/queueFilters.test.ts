import { describe, expect, it } from 'vitest';
import { STATUS_FACETS, type Job, type StatusCounts, type StatusFacet } from '$lib/api';
import {
	filterAndSort,
	jobStatusKey,
	loadFilters,
	matchedTotal,
	parseStatusParam,
	saveFilters,
	type QueueFilterState
} from '$lib/queueFilters';

function job(over: Partial<Job> & Pick<Job, 'id'>): Job {
	return {
		source: 'greenhouse',
		url: 'https://example.com',
		title: 'Engineer',
		remote: true,
		ingested_at: '2026-07-01T00:00:00Z',
		filter_status: 'passed',
		...over
	} as Job;
}

const base: QueueFilterState = {
	sortBy: 'score-desc',
	eases: new Set(),
	sources: new Set(),
	unscoredOnly: false,
	unemployment: new Set(),
	minScore: null
};

describe('jobStatusKey', () => {
	it('is "none" without an application, else the status', () => {
		expect(jobStatusKey(job({ id: 1 }))).toBe('none');
		expect(jobStatusKey(job({ id: 1, application: { status: 'applied' } as Job['application'] }))).toBe(
			'applied'
		);
	});
});

describe('filterAndSort', () => {
	const scored = (id: number, score: number | null) =>
		job({ id, score: score == null ? null : ({ score } as Job['score']) });

	it('sorts by score desc / asc, unscored last on desc', () => {
		const jobs = [scored(1, 50), scored(2, 90), scored(3, null)];
		expect(filterAndSort(jobs, { ...base, sortBy: 'score-desc' }).map((j) => j.id)).toEqual([
			2, 1, 3
		]);
		expect(filterAndSort(jobs, { ...base, sortBy: 'score-asc' }).map((j) => j.id)).toEqual([
			1, 2, 3
		]);
	});

	it('minScore drops jobs below the threshold (unscored treated as -1)', () => {
		const jobs = [scored(1, 40), scored(2, 80), scored(3, null)];
		expect(filterAndSort(jobs, { ...base, minScore: 60 }).map((j) => j.id)).toEqual([2]);
	});

	it('unscoredOnly keeps only jobs without a score', () => {
		const jobs = [scored(1, 40), scored(2, null)];
		expect(filterAndSort(jobs, { ...base, unscoredOnly: true }).map((j) => j.id)).toEqual([2]);
	});

	it('source facet filters by exact source', () => {
		const jobs = [job({ id: 1, source: 'lever' }), job({ id: 2, source: 'greenhouse' })];
		expect(
			filterAndSort(jobs, { ...base, sources: new Set(['lever']) }).map((j) => j.id)
		).toEqual([1]);
	});

	it('unemployment facet splits on the reported flag (no application = not used)', () => {
		const reported = { status: 'applied', used_for_unemployment: true } as Job['application'];
		const jobs = [
			job({ id: 1, application: reported }),
			job({ id: 2, application: { status: 'applied' } as Job['application'] }),
			job({ id: 3 })
		];
		expect(
			filterAndSort(jobs, { ...base, unemployment: new Set(['used']) }).map((j) => j.id)
		).toEqual([1]);
		expect(
			filterAndSort(jobs, { ...base, unemployment: new Set(['unused']) }).map((j) => j.id)
		).toEqual([2, 3]);
		// Both selected is the same as neither — the facet covers every job.
		expect(
			filterAndSort(jobs, { ...base, unemployment: new Set(['used', 'unused']) })
		).toHaveLength(3);
	});

	it('combines facets conjunctively', () => {
		const jobs = [
			job({ id: 1, source: 'lever', application: { status: 'applied' } as Job['application'] }),
			job({ id: 2, source: 'lever', application: { status: 'new' } as Job['application'] }),
			job({ id: 3, source: 'greenhouse', application: { status: 'applied' } as Job['application'] })
		];
		expect(
			filterAndSort(jobs, {
				...base,
				sources: new Set(['lever']),
				unscoredOnly: true
			}).map((j) => j.id)
		).toEqual([1, 2]);
	});

	it('does not mutate the input array', () => {
		const jobs = [scored(1, 10), scored(2, 90)];
		const copy = [...jobs];
		filterAndSort(jobs, { ...base, sortBy: 'score-desc' });
		expect(jobs).toEqual(copy);
	});
});

describe('loadFilters / saveFilters', () => {
	it('round-trips and tolerates a corrupt entry', () => {
		saveFilters({
			sortBy: 'title-asc',
			eases: [],
			sources: ['lever'],
			unscoredOnly: true,
			unemployment: [],
			minScoreInput: '70'
		});
		expect(loadFilters()).toMatchObject({ sortBy: 'title-asc', minScoreInput: '70' });

		localStorage.setItem('job-applier:queue-filters', '{broken');
		expect(loadFilters()).toBeNull();
	});
});

// Status moved out of filterAndSort and became a server-side query param carried
// in the URL, so selecting "applied" returns every applied job rather than the
// applied jobs that happened to be inside the fetched page. These two helpers are
// the seam: one reads the URL, the other reports the queue-wide match total.
describe('parseStatusParam', () => {
	it('keeps known facets, including "none"', () => {
		expect(parseStatusParam(['applied', 'none'])).toEqual(['applied', 'none']);
	});

	it('drops unknown values rather than throwing', () => {
		// A bookmark from before a status was renamed must degrade to "no filter".
		expect(parseStatusParam(['applied', 'bogus'])).toEqual(['applied']);
		expect(parseStatusParam(['nope'])).toEqual([]);
	});

	it('de-duplicates repeats', () => {
		expect(parseStatusParam(['applied', 'applied'])).toEqual(['applied']);
	});
});

describe('matchedTotal', () => {
	function counts(over: Partial<Record<StatusFacet, number>>): StatusCounts {
		const zeroed = Object.fromEntries(STATUS_FACETS.map((f) => [f, 0])) as Record<
			StatusFacet,
			number
		>;
		const merged = { ...zeroed, ...over };
		return { counts: merged, total: Object.values(merged).reduce((a, b) => a + b, 0) };
	}

	it('sums the selected facets', () => {
		const c = counts({ applied: 52, screening: 3, archived: 1442 });
		expect(matchedTotal(c, ['applied'])).toBe(52);
		expect(matchedTotal(c, ['applied', 'screening'])).toBe(55);
	});

	it('excludes archived when nothing is selected', () => {
		// No selection means the queue was loaded with exclude_archived, so the
		// comparable total has to leave archived out or it overstates wildly.
		const c = counts({ applied: 52, none: 14, archived: 1442 });
		expect(matchedTotal(c, [])).toBe(66);
	});

	it('is null when the counts call failed', () => {
		expect(matchedTotal(null, ['applied'])).toBeNull();
	});
});
