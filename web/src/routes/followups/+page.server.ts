import { api } from '$lib/api';
import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

const DAY_MS = 86_400_000;

function shiftDays(from: Date, days: number): string {
	return new Date(from.getTime() + days * DAY_MS).toISOString();
}

export const load: PageServerLoad = async ({ fetch }) => {
	const jobs = await api.getFollowups(fetch);
	return { jobs };
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
		await api.setFollowup(fetch, id, { next_followup_at });
		return { ok: true };
	},

	contacted: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		const now = new Date();
		await api.setFollowup(fetch, id, {
			last_contact_at: now.toISOString(),
			next_followup_at: shiftDays(now, 7)
		});
		return { ok: true };
	},

	rejected: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		// Rejection is both a terminal outcome and a status transition — flip both
		// so the row drops out of /followups AND the main "applied" filter.
		await api.setStatus(fetch, id, 'rejected', { outcome: 'rejected' });
		return { ok: true };
	},

	setOutcome: async ({ request, fetch }) => {
		const form = await request.formData();
		const id = parseId(form);
		if (id === null) return fail(400, { error: 'invalid id' });
		const outcome = String(form.get('outcome') ?? '').trim();
		if (!outcome) return fail(400, { error: 'outcome required' });
		await api.setFollowup(fetch, id, { outcome });
		return { ok: true };
	}
};
