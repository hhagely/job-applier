import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RequestEvent } from '@sveltejs/kit';

vi.mock('$lib/apiBase.server', () => ({ serverApiBase: () => 'http://test' }));

const { api } = vi.hoisted(() => ({
	api: {
		useResume: vi.fn(),
		uploadResume: vi.fn(),
		getStaleScoreCount: vi.fn(),
		getPendingScoreCount: vi.fn(),
		getProviders: vi.fn()
	}
}));
vi.mock('$lib/api', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api')>()),
	api
}));

import { ApiError } from '$lib/api';
import { actions } from './+page.server';

function event(form: Record<string, string | File>): RequestEvent {
	const fd = new FormData();
	for (const [k, v] of Object.entries(form)) fd.set(k, v);
	return { request: { formData: async () => fd }, fetch: vi.fn() } as unknown as RequestEvent;
}
const use = (form: Record<string, string>) =>
	(actions.use as (e: RequestEvent) => Promise<unknown>)(event(form));
const upload = () =>
	(actions.upload as (e: RequestEvent) => Promise<unknown>)(
		event({ file: new File(['%PDF'], 'r.pdf', { type: 'application/pdf' }) })
	);

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
});

describe('use resume action', () => {
	it.each<Record<string, string>>([{}, { id: 'abc' }, { id: '0' }])(
		'rejects %j without calling the API',
		async (form) => {
			const r = (await use(form)) as { status: number };
			expect(r.status).toBe(400);
			expect(api.useResume).not.toHaveBeenCalled();
		}
	);

	it('switches and raises the stale-scores prompt', async () => {
		api.useResume.mockResolvedValue({ id: 4 });
		api.getStaleScoreCount.mockResolvedValue({ count: 12 });
		api.getPendingScoreCount.mockResolvedValue({ count: 5, run_size: 5 });
		expect(await use({ id: '4' })).toEqual({
			ok: true,
			resume: { id: 4 },
			staleCount: 12,
			rescoreCount: 5,
			switched: true
		});
		expect(api.useResume).toHaveBeenCalledWith(expect.anything(), 'http://test', 4);
	});

	it('still reports the switch when only the stale count fails', async () => {
		api.useResume.mockResolvedValue({ id: 4 });
		api.getStaleScoreCount.mockRejectedValue(new ApiError('locked', 503));
		api.getPendingScoreCount.mockResolvedValue({ count: 5, run_size: 5 });
		expect(await use({ id: '4' })).toEqual({
			ok: true,
			resume: { id: 4 },
			staleCount: 0,
			rescoreCount: 5,
			switched: true
		});
	});

	it("refuses another profile's resume with the API's reason", async () => {
		api.useResume.mockRejectedValue(
			new ApiError('nope', 404, 'resume 9 not found for this profile')
		);
		const r = (await use({ id: '9' })) as { status: number; data: { error: string } };
		expect(r.status).toBe(404);
		expect(r.data.error).toContain('not found for this profile');
	});
});

describe('upload resume action', () => {
	it('returns the stale count and the run size side by side', async () => {
		api.uploadResume.mockResolvedValue({ id: 7 });
		api.getStaleScoreCount.mockResolvedValue({ count: 12 });
		api.getPendingScoreCount.mockResolvedValue({ count: 450, run_size: 200 });
		api.getProviders.mockResolvedValue({ selected: 'claude' });
		expect(await upload()).toEqual({
			ok: true,
			resume: { id: 7 },
			staleCount: 12,
			rescoreCount: 200,
			nextSteps: true,
			aiCanSuggest: true
		});
	});

	it('still reports the upload when the rescore count fails', async () => {
		api.uploadResume.mockResolvedValue({ id: 7 });
		api.getStaleScoreCount.mockResolvedValue({ count: 12 });
		api.getPendingScoreCount.mockRejectedValue(new ApiError('locked', 503));
		api.getProviders.mockResolvedValue({ selected: null });
		expect(await upload()).toMatchObject({ ok: true, staleCount: 12, rescoreCount: 0 });
	});
});
