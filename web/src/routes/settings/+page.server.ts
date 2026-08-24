import { api, errorReason, GHOSTED_DAYS_MAX, GHOSTED_DAYS_MIN } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { DEFAULT_GHOSTED_AFTER_DAYS } from '$lib/jobFilters';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const base = serverApiBase();
	const ai = await api.getProviders(fetch, base);
	// Same treatment as the version below: a preferences read that fails should
	// cost you that one card's stored value, not the whole Settings page.
	const prefs = await api
		.getPreferences(fetch, base)
		.catch(() => ({ ghosted_after_days: DEFAULT_GHOSTED_AFTER_DAYS }));
	// Running version for the About card. In the desktop shell the pill/popover use
	// window.desktop.version; this backs the same value in a plain browser.
	let version: string | null = null;
	try {
		version = (await api.getVersion(fetch, base)).version;
	} catch {
		version = null;
	}
	return { ai, prefs, version };
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
			return fail(422, { error: errorReason(e) });
		}
	},

	// One-click recovery from a scoring model the CLI won't accept. The dropdown's
	// "Default" option does the same thing, but only if you know that — this is the
	// action the failure message can point at by name.
	resetScoringModel: async ({ request, fetch }) => {
		const form = await request.formData();
		const name = String(form.get('name') ?? '');
		if (!name) return fail(400, { error: 'pick a provider' });
		try {
			const ai = await api.selectProvider(fetch, serverApiBase(), name, undefined, '');
			return { ok: true, ai, message: 'Scoring model reset to the provider default.' };
		} catch (e) {
			return fail(422, { error: errorReason(e) });
		}
	},

	// Distinct `prefsError` / `prefsMessage` keys rather than the shared
	// `error` / `message`: those are rendered inside the AI provider card, so
	// reusing them would report a saved follow-up setting under the wrong heading.
	savePreferences: async ({ request, fetch }) => {
		const form = await request.formData();
		const days = Number(form.get('ghosted_after_days'));
		if (!Number.isInteger(days) || days < GHOSTED_DAYS_MIN || days > GHOSTED_DAYS_MAX) {
			return fail(400, {
				prefsError: `Enter a whole number of days between ${GHOSTED_DAYS_MIN} and ${GHOSTED_DAYS_MAX}.`
			});
		}
		try {
			const prefs = await api.setPreferences(fetch, serverApiBase(), {
				ghosted_after_days: days
			});
			return {
				ok: true,
				prefs,
				prefsMessage: `Applications are offered up as ghosted after ${prefs.ghosted_after_days} days.`
			};
		} catch (e) {
			return fail(422, { prefsError: errorReason(e) });
		}
	},

	test: async ({ request, fetch }) => {
		const form = await request.formData();
		const prompt = (form.get('prompt') as string | null)?.trim() || undefined;
		try {
			const test = await api.testProvider(fetch, serverApiBase(), prompt);
			return { ok: true, test };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	}
};
