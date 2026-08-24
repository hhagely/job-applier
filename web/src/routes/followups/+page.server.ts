import { api, errorReason } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { DEFAULT_GHOSTED_AFTER_DAYS, isGhosted } from '$lib/jobFilters';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const DAY_MS = 86_400_000;

function shiftDays(from: Date, days: number): string {
	return new Date(from.getTime() + days * DAY_MS).toISOString();
}

export const load: PageServerLoad = async ({ fetch }) => {
	const base = serverApiBase();
	const [jobs, prefs] = await Promise.all([
		api.getFollowups(fetch, base),
		// A failed preferences read must not cost you the whole follow-up list —
		// fall back to the default cut-off and still render the page.
		api.getPreferences(fetch, base).catch(() => null)
	]);
	const ghostedAfterDays = prefs?.ghosted_after_days ?? DEFAULT_GHOSTED_AFTER_DAYS;
	// Split here rather than in the component: /api/followups returns every due
	// row with no limit, so this partition covers the whole set instead of
	// whatever happened to be in a fetched page.
	const ghosted = jobs.filter((j) => isGhosted(j, ghostedAfterDays));
	const ghostedIds = new Set(ghosted.map((j) => j.id));
	return {
		jobs,
		ghosted,
		due: jobs.filter((j) => !ghostedIds.has(j.id)),
		ghostedAfterDays
	};
};

function parseId(form: FormData): number | null {
	const id = Number(form.get('id'));
	return Number.isFinite(id) ? id : null;
}

export const actions: Actions = {
	snooze: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		const days = Number(form.get('days') ?? 7) || 7;
		const next_followup_at = shiftDays(new Date(), days);
		try {
			await api.setFollowup(fetch, serverApiBase(), id, { next_followup_at });
			return { ok: true };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},

	contacted: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		const now = new Date();
		try {
			await api.setFollowup(fetch, serverApiBase(), id, {
				last_contact_at: now.toISOString(),
				next_followup_at: shiftDays(now, 7)
			});
			return { ok: true };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},

	rejected: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		// Rejection is both a terminal outcome and a status transition — flip both
		// so the row drops out of /followups AND the main "applied" filter.
		try {
			await api.setStatus(fetch, serverApiBase(), id, 'rejected', { outcome: 'rejected' });
			return { ok: true };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},

	// Closing out a ghosted backlog one row at a time is the whole reason this
	// group exists, so it gets a bulk action. The ids come from the form rather
	// than being re-derived here on purpose: the button is labelled with a count
	// the user just read, and it should act on exactly those rows, not silently
	// sweep in a 32nd that crossed the threshold since the page rendered.
	noResponseAll: async ({ request, fetch }) => {
		const form = await request.formData();
		const ids = form
			.getAll('ids')
			.map((v) => Number(v))
			.filter((n) => Number.isFinite(n));
		if (ids.length === 0) return fail(400, { error: 'no applications to close out' });
		try {
			await api.bulkSetStatus(fetch, serverApiBase(), ids, 'no_response', {
				outcome: 'no response'
			});
			return { ok: true };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},

	noResponse: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		// Terminal like ?/rejected, but the employer never actually answered — a
		// separate status keeps it out of the dashboard's rejection count, and the
		// outcome is what drops the row from /followups.
		try {
			await api.setStatus(fetch, serverApiBase(), id, 'no_response', {
				outcome: 'no response'
			});
			return { ok: true };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	},

	setOutcome: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		const outcome = String(form.get('outcome') ?? '').trim();
		if (!outcome) return fail(400, { error: 'outcome required' });
		try {
			await api.setFollowup(fetch, serverApiBase(), id, { outcome });
			return { ok: true };
		} catch (e) {
			return fail(400, { error: errorReason(e) });
		}
	}
};
