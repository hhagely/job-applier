import { describe, expect, it } from 'vitest';

import { APPLICATION_STATUSES } from '$lib/api';

// Canary mirror of the Python `ApplicationStatus` enum (src/job_applier/models/db.py),
// asserted on the Python side by tests/test_status_contract.py. There is no shared
// codegen across the boundary, so keep this list in sync by hand — a mismatch here
// or in the paired pytest is the alarm that the two sides drifted.
const EXPECTED: string[] = [
	'new',
	'interested',
	'drafted',
	'applied',
	'screening',
	'interviewing',
	'rejected',
	'archived'
];

describe('status contract', () => {
	it('APPLICATION_STATUSES matches the canonical pipeline order', () => {
		expect(APPLICATION_STATUSES).toEqual(EXPECTED);
	});
});
