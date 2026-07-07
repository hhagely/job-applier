import { api, type SearchProfileBody } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const profile = await api.getSearchProfile(fetch, serverApiBase());
	const resume = await api.getCurrentResume(fetch, serverApiBase());
	return { profile, hasResume: resume !== null };
};

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
		await api.saveSearchProfile(fetch, serverApiBase(), merged);
		const profile = await api.clearRecommendations(fetch, serverApiBase());
		return { ok: true, profile, message: 'Recommendations applied.' };
	},

	rejectDraft: async ({ fetch }) => {
		const profile = await api.clearRecommendations(fetch, serverApiBase());
		return { ok: true, profile, message: 'Recommendations dismissed.' };
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
