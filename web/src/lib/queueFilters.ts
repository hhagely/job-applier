// Filter/sort logic + localStorage persistence for the Queue page. Pulled out of
// the Queue +page.svelte so that component only wires reactive $state to the UI;
// the (testable) filtering, comparison, and (de)serialization live here.
import { STATUS_FACETS, type Job, type StatusCounts, type StatusFacet } from '$lib/api';
import { isUsedForUnemployment } from '$lib/jobFilters';
import { sourceInfo, type Ease } from '$lib/sources';

export type SortKey = 'score-desc' | 'score-asc' | 'posted-desc' | 'ingested-desc' | 'title-asc';
/** @deprecated Use `StatusFacet` from $lib/api — status is now a server-side param. */
export type StatusFilter = StatusFacet;
export type UnempFilter = 'used' | 'unused';

// NOTE: `statuses` is deliberately absent. Status filtering is a server-side
// query param (?status=applied) so selecting a status returns every match, not
// the matches that happened to be in the fetched page — see /api/jobs.
export interface QueueFilterState {
	sortBy: SortKey;
	eases: Set<Ease>;
	sources: Set<string>;
	unscoredOnly: boolean;
	unemployment: Set<UnempFilter>;
	minScore: number | null;
}

/** The status facet a job falls under ('none' when it has no application row). */
export function jobStatusKey(job: Job): StatusFacet {
	return job.application?.status ?? 'none';
}

// --- Status selection (server-side, carried in the URL) -------------------

/**
 * Validate repeated `?status=` values into facets, dropping anything unknown.
 *
 * Unknown values are dropped rather than 400-ing: a stale bookmark from before a
 * status was renamed should degrade to "no status filter", not an error page.
 */
export function parseStatusParam(values: string[]): StatusFacet[] {
	const seen = new Set<StatusFacet>();
	for (const v of values) {
		if ((STATUS_FACETS as string[]).includes(v)) seen.add(v as StatusFacet);
	}
	return [...seen];
}

/**
 * How many postings the current status selection matches queue-wide.
 *
 * With no selection the queue is loaded with `exclude_archived`, so the
 * comparable total is everything except archived. Returns null when the counts
 * call failed, which the caller renders as "no total" rather than a wrong one.
 */
export function matchedTotal(
	counts: StatusCounts | null,
	selected: StatusFacet[]
): number | null {
	if (!counts) return null;
	if (selected.length === 0) return counts.total - (counts.counts.archived ?? 0);
	return selected.reduce((sum, facet) => sum + (counts.counts[facet] ?? 0), 0);
}

function dateVal(iso: string | null | undefined): number {
	return iso ? new Date(iso).getTime() : 0;
}

const COMPARATORS: Record<SortKey, (a: Job, b: Job) => number> = {
	'score-desc': (a, b) => (b.score?.score ?? -1) - (a.score?.score ?? -1),
	'score-asc': (a, b) => (a.score?.score ?? 999) - (b.score?.score ?? 999),
	'posted-desc': (a, b) => dateVal(b.posted_at) - dateVal(a.posted_at),
	'ingested-desc': (a, b) => dateVal(b.ingested_at) - dateVal(a.ingested_at),
	'title-asc': (a, b) => a.title.localeCompare(b.title)
};

/** Apply the active facet filters then sort. Pure — returns a new array. */
export function filterAndSort(jobs: Job[], f: QueueFilterState): Job[] {
	let list = jobs.slice();
	if (f.eases.size > 0) list = list.filter((j) => f.eases.has(sourceInfo(j.source).ease));
	if (f.sources.size > 0) list = list.filter((j) => f.sources.has(j.source));
	if (f.unscoredOnly) list = list.filter((j) => j.score == null);
	if (f.unemployment.size > 0) {
		list = list.filter((j) => f.unemployment.has(isUsedForUnemployment(j) ? 'used' : 'unused'));
	}
	if (f.minScore !== null) {
		const min = f.minScore;
		list = list.filter((j) => (j.score?.score ?? -1) >= min);
	}
	list.sort(COMPARATORS[f.sortBy]);
	return list;
}

// --- Persistence ---------------------------------------------------------

export const FILTERS_STORAGE_KEY = 'job-applier:queue-filters';

export interface PersistedFilters {
	sortBy: SortKey;
	eases: Ease[];
	sources: string[];
	unscoredOnly: boolean;
	unemployment: UnempFilter[];
	minScoreInput: string;
}

/** Read persisted filters, tolerating a missing / corrupt entry (returns null). */
export function loadFilters(): Partial<PersistedFilters> | null {
	try {
		const raw = localStorage.getItem(FILTERS_STORAGE_KEY);
		if (!raw) return null;
		const s = JSON.parse(raw);
		return typeof s === 'object' && s !== null ? s : null;
	} catch {
		return null;
	}
}

export function saveFilters(f: PersistedFilters): void {
	try {
		localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(f));
	} catch {
		/* quota / privacy mode — ignore */
	}
}
