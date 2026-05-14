import { api, type ApplicationStatus, type FilterStatus } from '$lib/api';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const VALID: FilterStatus[] = ['passed', 'manual'];

const VALID_STATUS: ApplicationStatus[] = [
	'new',
	'interested',
	'drafted',
	'applied',
	'rejected',
	'archived'
];

export const load: PageServerLoad = async ({ url, fetch }) => {
	const filterParam = url.searchParams.get('filter');
	const filter_status = (VALID.includes(filterParam as FilterStatus)
		? filterParam
		: 'passed') as FilterStatus;

	const all = await api.listJobs(fetch, { filter_status, limit: 200 });
	const jobs = all.filter((j) => j.application?.status !== 'archived');
	return { jobs, filter_status };
};

export const actions: Actions = {
	bulkStatus: async ({ request, fetch }) => {
		const form = await request.formData();
		const status = String(form.get('status') ?? '') as ApplicationStatus;
		if (!VALID_STATUS.includes(status)) return fail(400, { error: 'invalid status' });

		const ids = form
			.getAll('ids')
			.map((v) => Number(v))
			.filter((n) => Number.isFinite(n));
		if (ids.length === 0) return fail(400, { error: 'no jobs selected' });

		const followupRaw = (form.get('next_followup_at') as string | null) || '';
		const next_followup_at = followupRaw ? new Date(followupRaw).toISOString() : undefined;

		await api.bulkSetStatus(fetch, ids, status, { next_followup_at });
		return { ok: true, count: ids.length, status };
	}
};
