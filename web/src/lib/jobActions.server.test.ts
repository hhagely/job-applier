import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RequestEvent } from '@sveltejs/kit';

// Mock the server-only base + typed client so importing the .server module here
// doesn't pull in $env/dynamic/private and doesn't make real requests.
vi.mock('$lib/apiBase.server', () => ({ serverApiBase: () => 'http://test' }));

// hoisted so the vi.mock factory (also hoisted) can close over it.
const { api } = vi.hoisted(() => ({
	api: {
		setStatus: vi.fn(),
		setNotes: vi.fn(),
		setUnemployment: vi.fn(),
		renderDraft: vi.fn(),
		startDraft: vi.fn()
	}
}));
vi.mock('$lib/api', () => ({
	api,
	APPLICATION_STATUSES: [
		'new',
		'interested',
		'drafted',
		'applied',
		'screening',
		'interviewing',
		'rejected',
		'archived'
	]
}));

import { jobActions, parseFollowup } from './jobActions.server';

function event(form: Record<string, string> = {}, params: Record<string, string> = {}): RequestEvent {
	const fd = new FormData();
	for (const [k, v] of Object.entries(form)) fd.set(k, v);
	return {
		request: { formData: async () => fd },
		params,
		fetch: vi.fn()
	} as unknown as RequestEvent;
}

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
});

describe('parseFollowup', () => {
	it('returns undefined for absent/blank, ISO for valid, null for garbage', () => {
		expect(parseFollowup(null)).toBeUndefined();
		expect(parseFollowup('   ')).toBeUndefined();
		expect(parseFollowup('2026-07-20')).toBe(new Date('2026-07-20').toISOString());
		expect(parseFollowup('not-a-date')).toBeNull();
	});
});

describe('jobActions("field").setStatus', () => {
	const actions = jobActions('field');

	it('fails 400 on a non-numeric id', async () => {
		const r = (await actions.setStatus(event({ job_id: 'abc', status: 'applied' }))) as {
			status: number;
			data: { error: string };
		};
		expect(r.status).toBe(400);
		expect(r.data.error).toBe('invalid id');
		expect(api.setStatus).not.toHaveBeenCalled();
	});

	it('fails 400 on an unknown status', async () => {
		const r = (await actions.setStatus(event({ job_id: '1', status: 'bogus' }))) as {
			status: number;
			data: { error: string };
		};
		expect(r.status).toBe(400);
		expect(r.data.error).toBe('invalid status');
	});

	it('fails 400 on a malformed follow-up date instead of 500', async () => {
		const r = (await actions.setStatus(
			event({ job_id: '1', status: 'applied', next_followup_at: 'garbage' })
		)) as { status: number; data: { error: string } };
		expect(r.status).toBe(400);
		expect(r.data.error).toBe('invalid follow-up date');
		expect(api.setStatus).not.toHaveBeenCalled();
	});

	it('calls the API and returns ok on valid input', async () => {
		const r = await actions.setStatus(event({ job_id: '7', status: 'applied' }));
		expect(r).toEqual({ ok: true });
		expect(api.setStatus).toHaveBeenCalledWith(
			expect.anything(),
			'http://test',
			7,
			'applied',
			expect.objectContaining({ next_followup_at: undefined })
		);
	});
});

describe('jobActions("param").setStatus', () => {
	it('reads the id from the route param', async () => {
		const actions = jobActions('param');
		const r = await actions.setStatus(event({ status: 'interested' }, { id: '42' }));
		expect(r).toEqual({ ok: true });
		expect(api.setStatus).toHaveBeenCalledWith(
			expect.anything(),
			'http://test',
			42,
			'interested',
			expect.anything()
		);
	});
});

describe('jobActions("field").generateDraft', () => {
	it('maps a provider/resume failure to fail(409)', async () => {
		api.startDraft.mockRejectedValueOnce(new Error('no active resume'));
		const r = (await jobActions('field').generateDraft(event({ job_id: '3' }))) as {
			status: number;
			data: { error: string };
		};
		expect(r.status).toBe(409);
		expect(r.data.error).toBe('no active resume');
	});

	it('returns the task id + kind on success', async () => {
		api.startDraft.mockResolvedValueOnce({ task_id: 'tk1' });
		const r = await jobActions('field').generateDraft(event({ job_id: '3' }));
		expect(r).toEqual({ ok: true, task_id: 'tk1', kind: 'draft' });
	});
});
