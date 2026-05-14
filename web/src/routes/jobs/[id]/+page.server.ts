import { api, type ApplicationStatus } from '$lib/api';
import { error, fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const VALID_STATUS: ApplicationStatus[] = [
	'new',
	'interested',
	'drafted',
	'applied',
	'rejected',
	'archived'
];

export const load: PageServerLoad = async ({ params, fetch }) => {
	const id = Number(params.id);
	if (!Number.isFinite(id)) throw error(400, 'invalid id');
	try {
		const [job, draft, scoreHistory] = await Promise.all([
			api.getJob(fetch, id),
			api.getDraft(fetch, id),
			api.getScoreHistory(fetch, id)
		]);
		return { job, draft, scoreHistory };
	} catch (e) {
		throw error(404, (e as Error).message);
	}
};

export const actions: Actions = {
	setStatus: async ({ request, params, fetch }) => {
		const id = Number(params.id);
		const form = await request.formData();
		const status = String(form.get('status') ?? '') as ApplicationStatus;
		const notes = (form.get('notes') as string | null) || undefined;
		const followupRaw = (form.get('next_followup_at') as string | null) || '';
		const next_followup_at = followupRaw ? new Date(followupRaw).toISOString() : undefined;
		if (!VALID_STATUS.includes(status)) return fail(400, { error: 'invalid status' });
		await api.setStatus(fetch, id, status, { notes, next_followup_at });
		return { ok: true };
	},
	setNotes: async ({ request, params, fetch }) => {
		const id = Number(params.id);
		const form = await request.formData();
		const notes = String(form.get('notes') ?? '');
		await api.setNotes(fetch, id, notes);
		return { ok: true };
	},
	renderDraft: async ({ params, fetch }) => {
		const id = Number(params.id);
		try {
			await api.renderDraft(fetch, id);
			return { ok: true };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	}
};
