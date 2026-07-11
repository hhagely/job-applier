import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const ai = await api.getProviders(fetch, serverApiBase());
	return { ai };
};

export const actions: Actions = {
	select: async ({ request, fetch }) => {
		const form = await request.formData();
		const name = String(form.get('name') ?? '');
		const model = (form.get('model') as string | null)?.trim() || undefined;
		// Present-but-blank ("") clears the override; absent (null) leaves it untouched.
		const scoringRaw = form.get('scoring_model');
		const scoringModel = scoringRaw === null ? undefined : String(scoringRaw).trim();
		if (!name) return fail(400, { error: 'pick a provider' });
		try {
			const ai = await api.selectProvider(fetch, serverApiBase(), name, model, scoringModel);
			return { ok: true, ai, message: `Selected ${name}.` };
		} catch (e) {
			return fail(422, { error: (e as Error).message });
		}
	},

	test: async ({ request, fetch }) => {
		const form = await request.formData();
		const prompt = (form.get('prompt') as string | null)?.trim() || undefined;
		try {
			const test = await api.testProvider(fetch, serverApiBase(), prompt);
			return { ok: true, test };
		} catch (e) {
			return fail(400, { error: (e as Error).message });
		}
	}
};
