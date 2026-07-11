import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [ai, blacklist] = await Promise.all([
		api.getProviders(fetch, serverApiBase()),
		api.listBlacklist(fetch, serverApiBase())
	]);
	return { ai, blacklist };
};

/** FastAPI HTTPException bodies come back as `{"detail": "..."}` wrapped in the
 * api client's `API <path> -> <status>: <body>` message. Pull the detail out so
 * the UI shows a clean sentence instead of the raw envelope. */
function cleanError(e: unknown): string {
	const msg = (e as Error).message ?? 'request failed';
	const match = msg.match(/\{"detail":"(.+?)"\}/);
	return match ? match[1] : msg;
}

export const actions: Actions = {
	select: async ({ request, fetch }) => {
		const form = await request.formData();
		const name = String(form.get('name') ?? '');
		const model = (form.get('model') as string | null)?.trim() || undefined;
		if (!name) return fail(400, { error: 'pick a provider' });
		try {
			const ai = await api.selectProvider(fetch, serverApiBase(), name, model);
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
	},

	addBlacklist: async ({ request, fetch }) => {
		const form = await request.formData();
		const name = (form.get('company') as string | null)?.trim() ?? '';
		const reason = (form.get('reason') as string | null)?.trim() || undefined;
		if (!name) return fail(400, { blacklistError: 'Enter a company name.' });
		try {
			await api.addBlacklist(fetch, serverApiBase(), name, reason);
			return { blacklistOk: true, blacklistMessage: `Blacklisted ${name}.` };
		} catch (e) {
			return fail(422, { blacklistError: cleanError(e) });
		}
	},

	removeBlacklist: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('id'));
		if (!Number.isFinite(id)) return fail(400, { blacklistError: 'Bad entry id.' });
		try {
			await api.removeBlacklist(fetch, serverApiBase(), id);
			return { blacklistOk: true };
		} catch (e) {
			return fail(400, { blacklistError: cleanError(e) });
		}
	}
};
