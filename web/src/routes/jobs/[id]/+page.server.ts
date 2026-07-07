import { api, type ApplicationStatus } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { error, fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const VALID_STATUS: ApplicationStatus[] = [
	'new',
	'interested',
	'drafted',
	'applied',
	'screening',
	'interviewing',
	'rejected',
	'archived'
];

export const load: PageServerLoad = async ({ params, fetch }) => {
	const id = Number(params.id);
	if (!Number.isFinite(id)) throw error(400, 'invalid id');
	try {
		const [job, draft, scoreHistory] = await Promise.all([
			api.getJob(fetch, serverApiBase(), id),
			api.getDraft(fetch, serverApiBase(), id),
			api.getScoreHistory(fetch, serverApiBase(), id)
		]);
		const canonical =
			job.duplicate_of != null ? await api.getJob(fetch, serverApiBase(), job.duplicate_of) : null;
		return { job, draft, scoreHistory, canonical };
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
		await api.setStatus(fetch, serverApiBase(), id, status, { notes, next_followup_at });
		return { ok: true };
	},
	setNotes: async ({ request, params, fetch }) => {
		const id = Number(params.id);
		const form = await request.formData();
		const notes = String(form.get('notes') ?? '');
		await api.setNotes(fetch, serverApiBase(), id, notes);
		return { ok: true };
	},
	setUnemployment: async ({ request, params, fetch }) => {
		const id = Number(params.id);
		const form = await request.formData();
		const used = form.get('used') === 'true';
		await api.setUnemployment(fetch, serverApiBase(), id, used);
		return { ok: true };
	},
	renderDraft: async ({ params, fetch }) => {
		const id = Number(params.id);
		try {
			await api.renderDraft(fetch, serverApiBase(), id);
			return { ok: true };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	},

	// Start a background tailored-draft run (draft -> render PDFs -> re-score).
	// The mutation stays server-side; the client polls GET /api/ai/tasks/{id}.
	generateDraft: async ({ params, fetch }) => {
		const id = Number(params.id);
		try {
			const { task_id } = await api.startDraft(fetch, serverApiBase(), id);
			return { ok: true, task_id };
		} catch (e) {
			// 409 when no provider selected / no active resume.
			return fail(409, { error: (e as Error).message });
		}
	}
};
