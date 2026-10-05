import {
	api,
	APPLICATION_STATUSES,
	errorReason,
	type ApplicationStatus,
	type FilterStatus
} from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { matchedTotal, parseStatusParam } from '$lib/queueFilters';
import { jobActions, parseFollowup } from '$lib/jobActions.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const VALID: FilterStatus[] = ['passed', 'manual'];

/**
 * Cap on rows fetched for one queue view. Only the `archived` facet gets near it
 * (auto-archive puts thousands there); every other facet is far below, so the
 * page usually holds the COMPLETE match set and the remaining client-side facets
 * (ease / source / score) filter over all of it rather than over a window.
 */
const PAGE_LIMIT = 500;

export const load: PageServerLoad = async ({ url, fetch }) => {
	const filterParam = url.searchParams.get('filter');
	const filter_status = (VALID.includes(filterParam as FilterStatus)
		? filterParam
		: 'passed') as FilterStatus;
	const include_duplicates = url.searchParams.get('duplicates') === '1';
	// Archived jobs (incl. anything auto-archived for a sub-60 score) are normally
	// stripped from the queue; the "Show archived" toggle keeps them so the user can
	// inspect *why* a job was archived (its score + reasoning survive archiving).
	const include_archived = url.searchParams.get('archived') === '1';

	// Status is a SERVER-side filter carried in the URL, not a client-side pass over
	// whatever the page happened to load. Selecting "applied" must return every
	// applied job, including ones ingested long enough ago to fall outside a window.
	const statuses = parseStatusParam(url.searchParams.getAll('status'));

	// Excluding archived server-side is what keeps the row budget meaningful: the
	// auto-archived low scorers vastly outnumber live ones, so without this the
	// limit is spent almost entirely on rows the queue then throws away.
	// An explicit status selection speaks for itself and overrides both toggles.
	const exclude_archived = statuses.length === 0 && !include_archived;

	const base = serverApiBase();
	const [jobs, counts] = await Promise.all([
		api.listJobs(fetch, base, {
			filter_status,
			include_duplicates,
			exclude_archived,
			...(statuses.length > 0 ? { status: statuses } : {}),
			limit: PAGE_LIMIT
		}),
		// Chip counts are whole-queue totals. Degrade to null rather than failing
		// the page — the chips just lose their numbers.
		api.getStatusCounts(fetch, base, { filter_status, include_duplicates }).catch(() => null)
	]);

	return {
		jobs,
		statusCounts: counts,
		statuses,
		matched: matchedTotal(counts, statuses),
		limit: PAGE_LIMIT,
		filter_status,
		include_duplicates,
		include_archived
	};
};

export const actions: Actions = {
	bulkStatus: async ({ request, fetch }) => {
		const form = await request.formData();
		const status = String(form.get('status') ?? '') as ApplicationStatus;
		if (!APPLICATION_STATUSES.includes(status)) return fail(400, { error: 'invalid status' });

		const ids = form
			.getAll('ids')
			.map((v) => Number(v))
			.filter((n) => Number.isFinite(n));
		if (ids.length === 0) return fail(400, { error: 'no jobs selected' });

		const next_followup_at = parseFollowup(form.get('next_followup_at'));
		if (next_followup_at === null) return fail(400, { error: 'invalid follow-up date' });

		try {
			await api.bulkSetStatus(fetch, serverApiBase(), ids, status, { next_followup_at });
			return { ok: true, count: ids.length, status };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},
	bulkUnemployment: async ({ request, fetch }) => {
		const form = await request.formData();
		const used = form.get('used') === 'true';

		const ids = form
			.getAll('ids')
			.map((v) => Number(v))
			.filter((n) => Number.isFinite(n));
		if (ids.length === 0) return fail(400, { error: 'no jobs selected' });

		try {
			await api.bulkSetUnemployment(fetch, serverApiBase(), ids, used);
			return { ok: true, count: ids.length };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},

	// Kick off a background batch-draft of every job in the draft list, via the
	// configured AI provider. Client polls GET /api/ai/tasks/{id} for progress.
	draftBatch: async ({ request, fetch }) => {
		const form = await request.formData();
		const ids = form
			.getAll('ids')
			.map((v) => Number(v))
			.filter((n) => Number.isFinite(n));
		if (ids.length === 0) return fail(400, { error: 'draft list is empty' });
		try {
			const { task_id } = await api.startDraftBatch(fetch, serverApiBase(), ids);
			return { ok: true, task_id, kind: 'draft' };
		} catch (e) {
			// 409 when no provider selected / no active resume.
			return fail(409, { error: errorReason(e) });
		}
	},

	// The detail-pane status / notes / unemployment / draft mutations mirror the
	// /jobs/[id] actions so the master-detail pane is fully actionable without
	// navigating away. They read the target from a hidden `job_id` field.
	...jobActions('field')
};
