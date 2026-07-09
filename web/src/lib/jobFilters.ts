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

/** Whether the job was marked as reported to the unemployment office. */
export function isUsedForUnemployment(j: Job): boolean {
	return j.application?.used_for_unemployment ?? false;
}

/** A follow-up that is due (past its date and not already resolved by an outcome). */
export function isFollowupDue(j: Job, now = Date.now()): boolean {
	const due = j.application?.next_followup_at;
	if (!due || j.application?.outcome) return false;
	return new Date(due).getTime() <= now;
}
