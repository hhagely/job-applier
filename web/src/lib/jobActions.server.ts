// Shared per-job SvelteKit form actions. Both /jobs/[id] and the queue detail
// pane (/) expose the same status/notes/unemployment/draft mutations — the only
// difference is where the target id comes from: /jobs/[id] reads the route
// param, the queue pane reads a hidden `job_id` field. Everything else was
// duplicated verbatim, so it lives here and each route spreads in one variant.
import { api, APPLICATION_STATUSES, type ApplicationStatus } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { fail, type RequestEvent } from '@sveltejs/kit';

/** Where a route locates the target job id. */
export type JobIdSource = 'param' | 'field';

function resolveId(source: JobIdSource, event: RequestEvent, form: FormData): number {
	return source === 'param' ? Number(event.params.id) : Number(form.get('job_id'));
}

/** Parse the optional `next_followup_at` field into an ISO string.
 *  Returns `undefined` when absent/blank, or `null` when present but
 *  unparseable — so callers can `fail(400)` instead of letting a bad date make
 *  `new Date(x).toISOString()` throw a RangeError (an unhandled 500). */
export function parseFollowup(raw: FormDataEntryValue | null): string | null | undefined {
	const s = typeof raw === 'string' ? raw.trim() : '';
	if (!s) return undefined;
	const d = new Date(s);
	return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** The five shared job actions, wired to read the id from `source`. Spread the
 *  result into a route's `actions` (alongside any route-only actions). */
export function jobActions(source: JobIdSource) {
	return {
		setStatus: async (event: RequestEvent) => {
			const form = await event.request.formData();
			const id = resolveId(source, event, form);
			if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
			const status = String(form.get('status') ?? '') as ApplicationStatus;
			if (!APPLICATION_STATUSES.includes(status)) return fail(400, { error: 'invalid status' });
			const notes = (form.get('notes') as string | null) || undefined;
			const next_followup_at = parseFollowup(form.get('next_followup_at'));
			if (next_followup_at === null) return fail(400, { error: 'invalid follow-up date' });
			try {
				await api.setStatus(event.fetch, serverApiBase(), id, status, { notes, next_followup_at });
				return { ok: true };
			} catch (e) {
				return fail(400, { error: (e as Error).message });
			}
		},

		setNotes: async (event: RequestEvent) => {
			const form = await event.request.formData();
			const id = resolveId(source, event, form);
			if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
			const notes = String(form.get('notes') ?? '');
			try {
				await api.setNotes(event.fetch, serverApiBase(), id, notes);
				return { ok: true };
			} catch (e) {
				return fail(400, { error: (e as Error).message });
			}
		},

		setUnemployment: async (event: RequestEvent) => {
			const form = await event.request.formData();
			const id = resolveId(source, event, form);
			if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
			const used = form.get('used') === 'true';
			try {
				await api.setUnemployment(event.fetch, serverApiBase(), id, used);
				return { ok: true };
			} catch (e) {
				return fail(400, { error: (e as Error).message });
			}
		},

		renderDraft: async (event: RequestEvent) => {
			const form = await event.request.formData();
			const id = resolveId(source, event, form);
			if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
			try {
				await api.renderDraft(event.fetch, serverApiBase(), id);
				return { ok: true };
			} catch (e) {
				return fail(400, { error: (e as Error).message });
			}
		},

		// Start a background tailored-draft run (draft -> render PDFs -> re-score).
		// The mutation stays server-side; the client polls GET /api/ai/tasks/{id}.
		generateDraft: async (event: RequestEvent) => {
			const form = await event.request.formData();
			const id = resolveId(source, event, form);
			if (!Number.isFinite(id)) return fail(400, { error: 'invalid id' });
			try {
				const { task_id } = await api.startDraft(event.fetch, serverApiBase(), id);
				return { ok: true, task_id, kind: 'draft' as const };
			} catch (e) {
				// 409 when no provider selected / no active resume.
				return fail(409, { error: (e as Error).message });
			}
		}
	};
}
