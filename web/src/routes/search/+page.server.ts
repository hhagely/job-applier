import { api, type SearchProfileBody } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [profile, resume, blacklist] = await Promise.all([
		api.getSearchProfile(fetch, serverApiBase()),
		api.getCurrentResume(fetch, serverApiBase()),
		api.listBlacklist(fetch, serverApiBase())
	]);
	return { profile, hasResume: resume !== null, blacklist };
};

/** FastAPI HTTPException bodies come back as `{"detail": "..."}` wrapped in the
 * api client's `API <path> -> <status>: <body>` message. Pull the detail out so
 * the UI shows a clean sentence instead of the raw envelope. */
function cleanError(e: unknown): string {
	const msg = (e as Error).message ?? 'request failed';
	const match = msg.match(/\{"detail":"(.+?)"\}/);
	return match ? match[1] : msg;
}

function splitList(raw: FormDataEntryValue | null): string[] {
	if (typeof raw !== 'string') return [];
	return raw
		.split(/[\n,]/)
		.map((s) => s.trim())
		.filter(Boolean);
}

function readProfile(form: FormData): SearchProfileBody {
	return {
		role_titles: splitList(form.get('role_titles')),
		seniority_terms: splitList(form.get('seniority_terms')),
		required_tech: splitList(form.get('required_tech')),
		excluded_tech: splitList(form.get('excluded_tech')),
		extracted_skills: splitList(form.get('extracted_skills'))
	};
}

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		try {
			const profile = await api.saveSearchProfile(fetch, serverApiBase(), readProfile(form));
			return { ok: true, profile, message: 'Saved.' };
		} catch (e) {
			return fail(422, { error: (e as Error).message });
		}
	},

	// Analyze the resume and populate recommendations_draft (never the live fields).
	suggest: async ({ fetch }) => {
		try {
			const profile = await api.suggestRoles(fetch, serverApiBase());
			return { ok: true, profile, message: 'Recommendations ready — review below.' };
		} catch (e) {
			// 409 when no provider / no resume; 502 on a provider failure.
			return fail(422, { error: (e as Error).message });
		}
	},

	acceptDraft: async ({ request, fetch }) => {
		// Merge the LLM draft into the active fields. The draft is then cleared
		// so the UI doesn't keep nagging.
		const current = await api.getSearchProfile(fetch, serverApiBase());
		const draft = current.recommendations_draft;
		if (!draft) {
			return fail(409, { error: 'no draft to accept' });
		}
		const form = await request.formData();
		const mode = String(form.get('mode') ?? 'replace');
		const merged: SearchProfileBody =
			mode === 'append'
				? {
						role_titles: dedupe([...current.role_titles, ...draft.role_titles]),
						seniority_terms: dedupe([...current.seniority_terms, ...draft.seniority_terms]),
						required_tech: dedupe([...current.required_tech, ...draft.required_tech]),
						excluded_tech: dedupe([...current.excluded_tech, ...draft.excluded_tech]),
						extracted_skills: dedupe([...current.extracted_skills, ...draft.extracted_skills])
					}
				: {
						role_titles: draft.role_titles,
						seniority_terms: draft.seniority_terms,
						required_tech: draft.required_tech,
						excluded_tech: draft.excluded_tech,
						extracted_skills: draft.extracted_skills
					};
		try {
			await api.saveSearchProfile(fetch, serverApiBase(), merged);
			const profile = await api.clearRecommendations(fetch, serverApiBase());
			return { ok: true, profile, message: 'Recommendations applied.' };
		} catch (e) {
			return fail(422, { error: (e as Error).message });
		}
	},

	rejectDraft: async ({ fetch }) => {
		try {
			const profile = await api.clearRecommendations(fetch, serverApiBase());
			return { ok: true, profile, message: 'Recommendations dismissed.' };
		} catch (e) {
			return fail(422, { error: (e as Error).message });
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

function dedupe(items: string[]): string[] {
	const seen = new Set<string>();
	const out: string[] = [];
	for (const item of items) {
		const key = item.toLowerCase();
		if (seen.has(key)) continue;
		seen.add(key);
		out.push(item);
	}
	return out;
}
