import type { Job } from '$lib/api';

// Shared domain predicates for "what counts as archived / unreviewed / active".
// These encode a backend rule (the ApplicationStatus lifecycle) that the Queue,
// dashboard, and layout loaders all need; keeping them in one place means the
// definition of "unreviewed" changes once, not in three loaders. If/when the
// backend grows a dedicated filter for this, these become the single call site
// to swap.

/** A job the user (or auto-archive of a low score) moved out of the queue. */
export function isArchived(j: Job): boolean {
	return j.application?.status === 'archived';
}

/** Not yet triaged: no application row, or still the default "new" status. */
export function isUnreviewed(j: Job): boolean {
	const s = j.application?.status;
	return s == null || s === 'new';
}

/** The active queue: every non-archived job. */
export function activeJobs(jobs: Job[]): Job[] {
	return jobs.filter((j) => !isArchived(j));
}
