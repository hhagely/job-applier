import type { Job } from '$lib/api';
import { daysOverdue } from '$lib/date';

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

/**
 * Fallback ghost cut-off, for callers with no preferences loaded. The real value
 * is user-set on /settings and stored server-side (`ghosted_after_days`), so pass
 * it in wherever it is available — this constant only mirrors the backend default
 * in `contracts.py` for the case where the preferences fetch failed.
 *
 * Whatever the number, the ghost grouping is a nudge to close the row out and
 * never an automatic status change: nothing here mutates an application.
 */
export const DEFAULT_GHOSTED_AFTER_DAYS = 45;

/**
 * Whole days since the last sign of life: a reply if there was one, otherwise the
 * day the application went out. 0 when neither timestamp is set.
 *
 * Deliberately not measured from `next_followup_at` — that date is `applied_at + 7`
 * and moves every time the row is snoozed, so "45 days overdue" could mean anything
 * from seven weeks to four months.
 */
export function daysSinceContact(j: Job, now = Date.now()): number {
	return daysOverdue(j.application?.last_contact_at ?? j.application?.applied_at, now);
}

/**
 * Applied, silent for GHOSTED_AFTER_DAYS+, and never advanced past `applied`.
 * `screening`/`interviewing` are excluded on purpose: a human engaged there, so a
 * stale follow-up date means the row is out of date, not that you were ghosted.
 */
export function isGhosted(
	j: Job,
	afterDays: number = DEFAULT_GHOSTED_AFTER_DAYS,
	now = Date.now()
): boolean {
	return j.application?.status === 'applied' && daysSinceContact(j, now) >= afterDays;
}

/** A follow-up that is due (past its date and not already resolved by an outcome). */
export function isFollowupDue(j: Job, now = Date.now()): boolean {
	const due = j.application?.next_followup_at;
	if (!due || j.application?.outcome) return false;
	return new Date(due).getTime() <= now;
}
