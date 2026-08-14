import { api, errorReason } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { jobActions } from '$lib/jobActions.server';
import { error } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

/** The status to render this failure as. Collapsing everything to 404 made a
 *  transient backend failure ("the database is busy with a background job",
 *  a 503) read as "this posting no longer exists", and hid +error.svelte's
 *  retry hint, which only shows for 5xx. So pass the upstream status through —
 *  read structurally off ApiError.status, since the class identity isn't
 *  guaranteed across the server/client module boundary — and default anything
 *  without one (e.g. a network-level rejection) to 500. */
function loadStatus(e: unknown): number {
	const status = (e as { status?: unknown } | null)?.status;
	return typeof status === 'number' && status >= 400 && status <= 599 ? status : 500;
}

export const load: PageServerLoad = async ({ params, fetch }) => {
	const id = Number(params.id);
	if (!Number.isFinite(id)) throw error(400, 'invalid id');
	try {
		const [job, draft, scoreHistory] = await Promise.all([
			api.getJob(fetch, serverApiBase(), id),
			api.getDraft(fetch, serverApiBase(), id),
			api.getScoreHistory(fetch, serverApiBase(), id)
		]);
		const canonical =
			job.duplicate_of != null ? await api.getJob(fetch, serverApiBase(), job.duplicate_of) : null;
		return { job, draft, scoreHistory, canonical };
	} catch (e) {
		throw error(loadStatus(e), errorReason(e));
	}
};

// Status / notes / unemployment / draft mutations are shared with the queue
// detail pane; /jobs/[id] resolves the target from the route param.
export const actions: Actions = jobActions('param');
