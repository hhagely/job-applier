import { describe, expect, it, vi } from 'vitest';

import { API_BASE, api } from './api';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' },
		...init
	});
}

describe('api.listJobs', () => {
	it('serializes filter params into the query string', async () => {
		const fetchFn = vi.fn().mockResolvedValue(jsonResponse([]));
		await api.listJobs(fetchFn, { filter_status: 'passed', min_score: 70, limit: 25 });

		expect(fetchFn).toHaveBeenCalledTimes(1);
		const [url] = fetchFn.mock.calls[0];
		expect(url).toMatch(`${API_BASE}/api/jobs?`);
		const qs = new URL(url).searchParams;
		expect(qs.get('filter_status')).toBe('passed');
		expect(qs.get('min_score')).toBe('70');
		expect(qs.get('limit')).toBe('25');
	});

	it('omits undefined params', async () => {
		const fetchFn = vi.fn().mockResolvedValue(jsonResponse([]));
		await api.listJobs(fetchFn, {});
		const [url] = fetchFn.mock.calls[0];
		expect(new URL(url).searchParams.toString()).toBe('');
	});

	it('throws on non-2xx with the body in the message', async () => {
		const fetchFn = vi
			.fn()
			.mockResolvedValue(new Response('boom', { status: 500 }));
		await expect(api.listJobs(fetchFn)).rejects.toThrow(/500.*boom/);
	});
});

describe('api.getCurrentResume', () => {
	it('returns null on 404', async () => {
		const fetchFn = vi.fn().mockResolvedValue(new Response('', { status: 404 }));
		const result = await api.getCurrentResume(fetchFn);
		expect(result).toBeNull();
	});
});
