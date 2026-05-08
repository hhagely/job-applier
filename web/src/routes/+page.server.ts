import { api, type FilterStatus } from '$lib/api';
import type { PageServerLoad } from './$types';

const VALID: FilterStatus[] = ['passed', 'manual', 'dropped'];

export const load: PageServerLoad = async ({ url, fetch }) => {
	const filterParam = url.searchParams.get('filter');
	const filter_status = (VALID.includes(filterParam as FilterStatus)
		? filterParam
		: 'passed') as FilterStatus;

	const jobs = await api.listJobs(fetch, { filter_status, limit: 200 });
	return { jobs, filter_status };
};
