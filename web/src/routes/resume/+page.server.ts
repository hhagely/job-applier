import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const resume = await api.getCurrentResume(fetch, serverApiBase());
	return { resume };
};

export const actions: Actions = {
	upload: async ({ request, fetch }) => {
		const form = await request.formData();
		const file = form.get('file');
		if (!(file instanceof File) || file.size === 0) {
			return fail(400, { error: 'pick a PDF file first' });
		}
		try {
			const resume = await api.uploadResume(fetch, serverApiBase(), file);
			const { count: staleCount } = await api.getStaleScoreCount(fetch, serverApiBase());
			return { ok: true, resume, staleCount };
		} catch (e) {
			return fail(422, { error: (e as Error).message });
		}
	},

	// The two answers to the "N scores are now stale" prompt the upload raises.
	// Minor edit: keep the scores already paid for by re-stamping them onto the new
	// resume — no AI calls.
	keepScores: async ({ fetch }) => {
		try {
			const { count } = await api.adoptScores(fetch, serverApiBase());
			return { ok: true, kept: count };
		} catch (e) {
			return fail(409, { error: (e as Error).message });
		}
	},

	// Significant edit: re-run scoring over the stale rows (and anything unscored).
	// Runs as a background task; the client tracks it off the shared event stream.
	rescoreStale: async ({ fetch }) => {
		try {
			const { task_id } = await api.startScorePending(fetch, serverApiBase(), {
				include_stale: true
			});
			return { ok: true, task_id };
		} catch (e) {
			// 409 when no provider is selected / no active resume.
			return fail(409, { error: (e as Error).message });
		}
	}
};
