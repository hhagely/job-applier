import { describe, it, expect } from 'vitest';
import { fmtDate, fmtDateTime, daysOverdue, defaultFollowupDate, DAY_MS } from './date';

describe('fmtDate', () => {
	it('returns an em dash for null/undefined/empty', () => {
		expect(fmtDate(null)).toBe('—');
		expect(fmtDate(undefined)).toBe('—');
		expect(fmtDate('')).toBe('—');
	});
	it('formats a real date to something other than the dash', () => {
		expect(fmtDate('2026-04-02T00:00:00Z')).not.toBe('—');
	});
});

describe('fmtDateTime', () => {
	it('returns empty string for null/empty', () => {
		expect(fmtDateTime(null)).toBe('');
		expect(fmtDateTime('')).toBe('');
	});
});

describe('daysOverdue', () => {
	const now = 10 * DAY_MS;
	it('counts whole days between the date and now', () => {
		expect(daysOverdue(new Date(8 * DAY_MS).toISOString(), now)).toBe(2);
	});
	it('clamps future dates to 0', () => {
		expect(daysOverdue(new Date(12 * DAY_MS).toISOString(), now)).toBe(0);
	});
	it('returns 0 for null/empty', () => {
		expect(daysOverdue(null, now)).toBe(0);
		expect(daysOverdue(undefined, now)).toBe(0);
	});
});

describe('defaultFollowupDate', () => {
	const now = Date.parse('2026-04-02T12:00:00Z');
	it('returns yyyy-mm-dd N days out (default 7)', () => {
		expect(defaultFollowupDate(7, now)).toBe('2026-04-09');
	});
	it('returns today for 0 days', () => {
		expect(defaultFollowupDate(0, now)).toBe('2026-04-02');
	});
});
