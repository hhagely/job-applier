import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import { jobActions } from '$lib/jobActions.server';
import { error } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

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
		throw error(404, (e as Error).message);
	}
};

// Status / notes / unemployment / draft mutations are shared with the queue
// detail pane; /jobs/[id] resolves the target from the route param.
export const actions: Actions = jobActions('param');
