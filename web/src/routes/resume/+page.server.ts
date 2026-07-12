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
	}
};
