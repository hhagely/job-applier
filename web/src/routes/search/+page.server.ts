import { api, errorReason, type SearchProfileBody } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
	const [profile, resume, blacklist, coverage, watched] = await Promise.all([
		api.getSearchProfile(fetch, serverApiBase()),
		api.getCurrentResume(fetch, serverApiBase()),
		api.listBlacklist(fetch, serverApiBase()),
		api.getCompanyCoverage(fetch, serverApiBase()),
		api.listWatchedCompanies(fetch, serverApiBase())
	]);
	return { profile, hasResume: resume !== null, blacklist, coverage, watched };
};

function splitList(raw: FormDataEntryValue | null): string[] {
	if (typeof raw !== 'string') return [];
	return raw
		.split(/[\n,]/)
		.map((s) => s.trim())
		.filter(Boolean);
}

function readProfile(form: FormData): SearchProfileBody {
	const homeState = form.get('home_state');
	return {
		role_titles: splitList(form.get('role_titles')),
		seniority_terms: splitList(form.get('seniority_terms')),
		required_tech: splitList(form.get('required_tech')),
		excluded_tech: splitList(form.get('excluded_tech')),
		extracted_skills: splitList(form.get('extracted_skills')),
		home_state: typeof homeState === 'string' && homeState.trim() ? homeState.trim() : null
	};
}

export const actions: Actions = {
	save: async ({ request, fetch }) => {
		const form = await request.formData();
		try {
			const profile = await api.saveSearchProfile(fetch, serverApiBase(), readProfile(form));
			return { ok: true, profile, message: 'Saved.' };
		} catch (e) {
			return fail(422, { error: errorReason(e) });
		}
	},

	// Analyze the resume and populate recommendations_draft (never the live fields).
	suggest: async ({ fetch }) => {
		try {
			const profile = await api.suggestRoles(fetch, serverApiBase());
			return { ok: true, profile, message: 'Recommendations ready — review below.' };
		} catch (e) {
			// 409 when no provider / no resume; 502 on a provider failure.
			return fail(422, { error: errorReason(e) });
		}
	},

	acceptDraft: async ({ request, fetch }) => {
		// Merge the LLM draft into the active fields. The draft is then cleared
		// so the UI doesn't keep nagging. The whole body is inside the try so an
		// API failure (or a draft missing an expected list) returns fail(), not 500.
		try {
			const current = await api.getSearchProfile(fetch, serverApiBase());
			const draft = current.recommendations_draft;
			if (!draft) {
				return fail(409, { error: 'no draft to accept' });
			}
			const form = await request.formData();
			const mode = String(form.get('mode') ?? 'replace');
			// The PUT is a full replace, so carry home_state through unchanged —
			// accepting role suggestions must not wipe the user's configured state.
			const merged: SearchProfileBody =
				mode === 'append'
					? {
							role_titles: dedupe([...(current.role_titles ?? []), ...(draft.role_titles ?? [])]),
							seniority_terms: dedupe([
								...(current.seniority_terms ?? []),
								...(draft.seniority_terms ?? [])
							]),
							required_tech: dedupe([
								...(current.required_tech ?? []),
								...(draft.required_tech ?? [])
							]),
							excluded_tech: dedupe([
								...(current.excluded_tech ?? []),
								...(draft.excluded_tech ?? [])
							]),
							extracted_skills: dedupe([
								...(current.extracted_skills ?? []),
								...(draft.extracted_skills ?? [])
							]),
							home_state: current.home_state
						}
					: {
							role_titles: draft.role_titles ?? [],
							seniority_terms: draft.seniority_terms ?? [],
							required_tech: draft.required_tech ?? [],
							excluded_tech: draft.excluded_tech ?? [],
							extracted_skills: draft.extracted_skills ?? [],
							home_state: current.home_state
						};
			await api.saveSearchProfile(fetch, serverApiBase(), merged);
			const profile = await api.clearRecommendations(fetch, serverApiBase());
			return { ok: true, profile, message: 'Recommendations applied.' };
		} catch (e) {
			return fail(422, { error: errorReason(e) });
		}
	},

	rejectDraft: async ({ fetch }) => {
		try {
			const profile = await api.clearRecommendations(fetch, serverApiBase());
			return { ok: true, profile, message: 'Recommendations dismissed.' };
		} catch (e) {
			return fail(422, { error: errorReason(e) });
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
			return fail(422, { blacklistError: errorReason(e) });
		}
	},

	// Kick off a background pass that finds company job boards we aren't watching.
	// Mutation stays server-side per convention; progress arrives over the shared
	// task stream, same as the dashboard's scrape.
	refreshCompanies: async ({ request, fetch }) => {
		const form = await request.formData();
		const reverify = form.get('reverify') === 'on';
		try {
			const { task_id } = await api.startCompanyRefresh(fetch, serverApiBase(), reverify);
			return { ok: true, task_id };
		} catch (e) {
			return fail(500, { coverageError: errorReason(e) });
		}
	},

	// Add one company to the searched list. The backend probes the live ATS APIs,
	// so this action is seconds-slow by nature — the UI shows a busy state.
	addCompany: async ({ request, fetch }) => {
		const form = await request.formData();
		const query = (form.get('query') as string | null)?.trim() ?? '';
		if (!query) return fail(400, { companyError: 'Enter a company name or job-board URL.' });
		try {
			const result = await api.addWatchedCompany(fetch, serverApiBase(), query);
			// "already searched" is a 200 with a notice, not a failure — the company
			// is in the list either way, so the user just needs telling.
			return {
				companyOk: true,
				companyAlready: result.status === 'already_searched',
				companyMessage: result.message
			};
		} catch (e) {
			return fail(422, { companyError: errorReason(e) });
		}
	},

	removeCompany: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = Number(form.get('id'));
		if (!Number.isFinite(id)) return fail(400, { companyError: 'Bad company id.' });
		try {
			await api.removeWatchedCompany(fetch, serverApiBase(), id);
			return { companyOk: true, companyAlready: false, companyMessage: '' };
		} catch (e) {
			return fail(400, { companyError: errorReason(e) });
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
			return fail(400, { blacklistError: errorReason(e) });
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
