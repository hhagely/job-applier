import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/apiBase.server', () => ({ serverApiBase: () => 'http://test' }));

const { api } = vi.hoisted(() => ({
	api: {
		listJobs: vi.fn(),
		getFollowups: vi.fn(),
		getStatusCounts: vi.fn(),
		getPendingScoreCount: vi.fn()
	}
}));
vi.mock('$lib/api', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api')>()),
	api
}));

import { ApiError } from '$lib/api';
import { load } from './+page.server';

type Loaded = { pending: number; pendingTotal: number };
const run = () => load({ fetch: vi.fn() } as never) as Promise<Loaded>;

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
	api.listJobs.mockResolvedValue([]);
	api.getFollowups.mockResolvedValue([]);
	api.getStatusCounts.mockResolvedValue(null);
});

describe('dashboard load', () => {
	it("labels the button with one run's size and the full backlog", async () => {
		api.getPendingScoreCount.mockResolvedValue({ count: 450, run_size: 200 });
		expect(await run()).toMatchObject({ pending: 200, pendingTotal: 450 });
		expect(api.getPendingScoreCount).toHaveBeenCalledWith(expect.anything(), 'http://test', true);
	});

	it('still renders when the count fails', async () => {
		api.getPendingScoreCount.mockRejectedValue(new ApiError('locked', 503));
		expect(await run()).toMatchObject({ pending: 0, pendingTotal: 0 });
	});
});
