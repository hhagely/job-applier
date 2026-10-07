import { api, errorReason } from '$lib/api';
import { activeProfile } from '$lib/profiles';
import { serverApiBase } from '$lib/apiBase.server';
import { failStatus, formId } from '$lib/forms.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, parent }) => {
	// Both are the active profile's: the API scopes resumes to it.
	const [resume, resumes, { profiles }] = await Promise.all([
		api.getCurrentResume(fetch, serverApiBase()),
		api.listResumes(fetch, serverApiBase()).catch(() => []),
		parent()
	]);
	const profileName = activeProfile(profiles)?.name ?? null;
	return { resume, resumes, profileName };
};

/** Can we offer "let the AI read this resume and suggest search criteria"?
 * `/api/ai/providers` reports `selected` as null when the chosen CLI is no longer
 * on PATH, so this is "configured *and* detected" — unlike the layout's cheap
 * `aiProvider`, which is the persisted choice alone. It costs a `--version` probe
 * per provider, so it only runs on the post-upload actions, never on page load. */
async function aiCanSuggest(fetch: typeof globalThis.fetch): Promise<boolean> {
	try {
		const { selected } = await api.getProviders(fetch, serverApiBase());
		return Boolean(selected);
	} catch {
		return false; // a failed probe just means we don't promote the AI path
	}
}

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
			const canSuggest = await aiCanSuggest(fetch);
			return { ok: true, resume, staleCount, nextSteps: true, aiCanSuggest: canSuggest };
		} catch (e) {
			return fail(failStatus(e), { error: errorReason(e) });
		}
	},

	// Switch this profile to another of its uploads. Scores made against the
	// previous one go stale, so this raises the same keep / re-score prompt.
	use: async ({ request, fetch }) => {
		const id = formId(await request.formData());
		if (id === null) return fail(400, { error: 'Bad resume id.' });
		let resume;
		try {
			resume = await api.useResume(fetch, serverApiBase(), id);
		} catch (e) {
			return fail(failStatus(e), { error: errorReason(e) });
		}
		// The switch already happened: a failed count only costs the prompt.
		const { count: staleCount } = await api
			.getStaleScoreCount(fetch, serverApiBase())
			.catch(() => ({ count: 0 }));
		return { ok: true, resume, staleCount, switched: true };
	},

	// The two answers to the "N scores are now stale" prompt the upload raises.
	// Both also carry `nextSteps` so the "head to your search profile" callout
	// survives the prompt being answered — it's the same post-upload moment.
	//
	// Minor edit: keep the scores already paid for by re-stamping them onto the new
	// resume — no AI calls.
	keepScores: async ({ fetch }) => {
		try {
			const { count } = await api.adoptScores(fetch, serverApiBase());
			const canSuggest = await aiCanSuggest(fetch);
			return { ok: true, kept: count, nextSteps: true, aiCanSuggest: canSuggest };
		} catch (e) {
			return fail(failStatus(e), { error: errorReason(e) });
		}
	},

	// Significant edit: re-run scoring over the stale rows (and anything unscored).
	// Runs as a background task; the client tracks it off the shared event stream.
	rescoreStale: async ({ fetch }) => {
		try {
			const { task_id } = await api.startScorePending(fetch, serverApiBase(), {
				include_stale: true
			});
			// Scoring only starts with a working provider, so suggestions are available too.
			return { ok: true, task_id, nextSteps: true, aiCanSuggest: true };
		} catch (e) {
			// 409 when no provider is selected / no active resume.
			return fail(failStatus(e), { error: errorReason(e) });
		}
	}
};
