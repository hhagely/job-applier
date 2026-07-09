// Shared date formatting/arithmetic helpers. The `now` parameters make the
// arithmetic ones pure and testable; call sites pass nothing and get Date.now().

export const DAY_MS = 86_400_000;

/** Locale date (no time), or an em dash for null/empty — the row format used by
 *  the queue, dashboard, and followups lists. */
export function fmtDate(iso: string | null | undefined): string {
	return iso ? new Date(iso).toLocaleDateString() : '—';
}

/** Locale date + time, or empty string — the detail-view timestamp format. */
export function fmtDateTime(iso: string | null | undefined): string {
	return iso ? new Date(iso).toLocaleString() : '';
}

/** Whole days between `iso` and now, clamped at 0 (0 for null/empty). */
export function daysOverdue(iso: string | null | undefined, now: number = Date.now()): number {
	if (!iso) return 0;
	return Math.max(0, Math.floor((now - new Date(iso).getTime()) / DAY_MS));
}

/** `yyyy-mm-dd` for `days` from now — the default value of a follow-up date input. */
export function defaultFollowupDate(days = 7, now: number = Date.now()): string {
	return new Date(now + days * DAY_MS).toISOString().slice(0, 10);
}

/** Coarse "how long ago" label for a past timestamp — the queue detail-pane
 *  ingested/posted format ("today", "3 days ago", "2 months ago"). */
export function relTime(iso: string, now: number = Date.now()): string {
	const days = Math.floor((now - new Date(iso).getTime()) / DAY_MS);
	if (days <= 0) return 'today';
	if (days === 1) return '1 day ago';
	if (days < 30) return `${days} days ago`;
	const months = Math.floor(days / 30);
	return `${months} month${months === 1 ? '' : 's'} ago`;
}

/** Follow-up overdue label from a whole-day count — "due today" / "5d overdue".
 *  Pair with {@link daysOverdue}. */
export function formatOverdue(days: number): string {
	return days === 0 ? 'due today' : `${days}d overdue`;
}
