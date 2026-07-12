import { describe, expect, it } from 'vitest';
import type { Application, Job } from '$lib/api';
import {
	activeJobs,
	isArchived,
	isFollowupDue,
	isUnreviewed,
	isUsedForUnemployment
} from '$lib/jobFilters';

/** Build a Job with only the application fields a test cares about. Pass `null`
 *  for "no application row yet". */
function job(app: Partial<Application> | null, id = 1): Job {
	const application =
		app === null
			? null
			: ({
					status: 'new',
					updated_at: '2026-07-01T00:00:00Z',
					used_for_unemployment: false,
					...app
				} as Application);
	return {
		id,
		source: 'test',
		url: 'https://example.com',
		title: 'Engineer',
		remote: true,
		ingested_at: '2026-07-01T00:00:00Z',
		filter_status: 'passed',
		application
	} as Job;
}

describe('isArchived', () => {
	it('is true only for the archived status', () => {
		expect(isArchived(job({ status: 'archived' }))).toBe(true);
		expect(isArchived(job({ status: 'interested' }))).toBe(false);
		expect(isArchived(job(null))).toBe(false);
	});
});

describe('isUnreviewed', () => {
	it('treats no-application and the default "new" status as unreviewed', () => {
		expect(isUnreviewed(job(null))).toBe(true);
		expect(isUnreviewed(job({ status: 'new' }))).toBe(true);
		expect(isUnreviewed(job({ status: 'interested' }))).toBe(false);
	});
});

describe('activeJobs', () => {
	it('drops archived jobs and keeps the rest', () => {
		const jobs = [
			job({ status: 'new' }, 1),
			job({ status: 'archived' }, 2),
			job({ status: 'applied' }, 3)
		];
		expect(activeJobs(jobs).map((j) => j.id)).toEqual([1, 3]);
	});
});

describe('isUsedForUnemployment', () => {
	it('reflects the flag and defaults to false', () => {
		expect(isUsedForUnemployment(job({ used_for_unemployment: true }))).toBe(true);
		expect(isUsedForUnemployment(job({ used_for_unemployment: false }))).toBe(false);
		expect(isUsedForUnemployment(job(null))).toBe(false);
	});
});

describe('isFollowupDue', () => {
	const now = Date.parse('2026-07-11T00:00:00Z');
	const past = '2026-07-01T00:00:00Z';
	const future = '2026-07-20T00:00:00Z';

	it('is false without a follow-up date or application', () => {
		expect(isFollowupDue(job(null), now)).toBe(false);
		expect(isFollowupDue(job({ status: 'applied' }), now)).toBe(false);
	});

	it('is true when the date has passed and no outcome resolved it', () => {
		expect(isFollowupDue(job({ next_followup_at: past }), now)).toBe(true);
	});

	it('is false when the date is still in the future', () => {
		expect(isFollowupDue(job({ next_followup_at: future }), now)).toBe(false);
	});

	it('is false once an outcome is recorded, even if overdue', () => {
		expect(isFollowupDue(job({ next_followup_at: past, outcome: 'replied' }), now)).toBe(false);
	});
});
