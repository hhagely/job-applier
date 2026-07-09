import { api, APPLICATION_STATUSES, type ApplicationStatus, type FilterStatus } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { activeJobs } from '$lib/jobFilters';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const VALID: FilterStatus[] = ['passed', 'manual'];

export const load: PageServerLoad = async ({ url, fetch }) => {
	const filterParam = url.searchParams.get('filter');
	const filter_status = (VALID.includes(filterParam as FilterStatus)
		? filterParam
		: 'passed') as FilterStatus;
	const include_duplicates = url.searchParams.get('duplicates') === '1';

	const all = await api.listJobs(fetch, serverApiBase(), {
		filter_status,
		include_duplicates,
		limit: 200
	});
	const jobs = activeJobs(all);
	return { jobs, filter_status, include_duplicates };
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

		const followupRaw = (form.get('next_followup_at') as string | null) || '';
		const next_followup_at = followupRaw ? new Date(followupRaw).toISOString() : undefined;

		try {
			await api.bulkSetStatus(fetch, serverApiBase(), ids, status, { next_followup_at });
			return { ok: true, count: ids.length, status };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
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
			return fail(400, { error: (e as Error).message });
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
			return fail(409, { error: (e as Error).message });
		}
	},

	// --- Inline detail-pane mutations (mirror the /jobs/[id] actions so the queue
	// master-detail pane is fully actionable without navigating away). Each reads
	// the target from a hidden `job_id` field rather than a route param. ---
	setStatus: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('job_id'));
		if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
		const status = String(form.get('status') ?? '') as ApplicationStatus;
		if (!APPLICATION_STATUSES.includes(status)) return fail(400, { error: 'invalid status' });
		const followupRaw = (form.get('next_followup_at') as string | null) || '';
		const next_followup_at = followupRaw ? new Date(followupRaw).toISOString() : undefined;
		try {
			await api.setStatus(fetch, serverApiBase(), id, status, { next_followup_at });
			return { ok: true };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	},
	setNotes: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('job_id'));
		if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
		const notes = String(form.get('notes') ?? '');
		try {
			await api.setNotes(fetch, serverApiBase(), id, notes);
			return { ok: true };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	},
	setUnemployment: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('job_id'));
		if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
		const used = form.get('used') === 'true';
		try {
			await api.setUnemployment(fetch, serverApiBase(), id, used);
			return { ok: true };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	},
	renderDraft: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('job_id'));
		if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
		try {
			await api.renderDraft(fetch, serverApiBase(), id);
			return { ok: true };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	},
	generateDraft: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('job_id'));
		if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
		try {
			const { task_id } = await api.startDraft(fetch, serverApiBase(), id);
			return { ok: true, task_id, kind: 'draft' };
		} catch (e) {
			// 409 when no provider selected / no active resume.
			return fail(409, { error: (e as Error).message });
		}
	}
};
