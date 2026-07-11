import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, getApiBase } from './api';

const TEST_BASE = 'http://127.0.0.1:8000';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' },
		...init
	});
}

describe('api.listJobs', () => {
	it('serializes filter params into the query string against the given base', async () => {
		const fetchFn = vi.fn().mockResolvedValue(jsonResponse([]));
		await api.listJobs(fetchFn, TEST_BASE, { filter_status: 'passed', min_score: 70, limit: 25 });

		expect(fetchFn).toHaveBeenCalledTimes(1);
		const [url] = fetchFn.mock.calls[0];
		expect(url).toMatch(`${TEST_BASE}/api/jobs?`);
		const qs = new URL(url).searchParams;
		expect(qs.get('filter_status')).toBe('passed');
		expect(qs.get('min_score')).toBe('70');
		expect(qs.get('limit')).toBe('25');
	});

	it('uses the base it is passed', async () => {
		const fetchFn = vi.fn().mockResolvedValue(jsonResponse([]));
		await api.listJobs(fetchFn, 'http://127.0.0.1:53112', {});
		const [url] = fetchFn.mock.calls[0];
		expect(url.startsWith('http://127.0.0.1:53112/api/jobs')).toBe(true);
	});

	it('omits undefined params', async () => {
		const fetchFn = vi.fn().mockResolvedValue(jsonResponse([]));
		await api.listJobs(fetchFn, TEST_BASE, {});
		const [url] = fetchFn.mock.calls[0];
		expect(new URL(url).searchParams.toString()).toBe('');
	});

	it('throws on non-2xx with the body in the message', async () => {
		const fetchFn = vi.fn().mockResolvedValue(new Response('boom', { status: 500 }));
		await expect(api.listJobs(fetchFn, TEST_BASE)).rejects.toThrow(/500.*boom/);
	});
});

describe('api.getCurrentResume', () => {
	it('returns null on 404', async () => {
		const fetchFn = vi.fn().mockResolvedValue(new Response('', { status: 404 }));
		const result = await api.getCurrentResume(fetchFn, TEST_BASE);
		expect(result).toBeNull();
	});
});

describe('api PDF url helpers', () => {
	it('build absolute URLs from the given base', () => {
		expect(api.resumePdfUrl(TEST_BASE)).toBe(`${TEST_BASE}/api/resume/current/pdf`);
		expect(api.draftResumePdfUrl(TEST_BASE, 5)).toBe(`${TEST_BASE}/api/jobs/5/draft/resume.pdf`);
		expect(api.draftCoverLetterPdfUrl(TEST_BASE, 5)).toBe(
			`${TEST_BASE}/api/jobs/5/draft/cover-letter.pdf`
		);
	});
});

describe('api blacklist', () => {
	it('posts name and reason to /api/blacklist', async () => {
		const fetchFn = vi.fn().mockResolvedValue(
			jsonResponse({ id: 1, name: 'Evil Corp', normalized_name: 'evil', reason: 'x', created_at: '' })
		);
		await api.addBlacklist(fetchFn, TEST_BASE, 'Evil Corp', 'x');

		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe(`${TEST_BASE}/api/blacklist`);
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body)).toEqual({ name: 'Evil Corp', reason: 'x' });
	});

	it('deletes by id and tolerates a 404', async () => {
		const fetchFn = vi.fn().mockResolvedValue(new Response('', { status: 404 }));
		await expect(api.removeBlacklist(fetchFn, TEST_BASE, 7)).resolves.toBeUndefined();
		const [url, init] = fetchFn.mock.calls[0];
		expect(url).toBe(`${TEST_BASE}/api/blacklist/7`);
		expect(init.method).toBe('DELETE');
	});

	it('throws on a non-404 delete error', async () => {
		const fetchFn = vi.fn().mockResolvedValue(new Response('boom', { status: 500 }));
		await expect(api.removeBlacklist(fetchFn, TEST_BASE, 7)).rejects.toThrow(/500.*boom/);
	});
});

describe('getApiBase', () => {
	afterEach(() => {
		delete (window as unknown as { __API_BASE__?: string }).__API_BASE__;
	});

	it('returns the injected window.__API_BASE__', () => {
		(window as unknown as { __API_BASE__?: string }).__API_BASE__ = 'http://127.0.0.1:41234';
		expect(getApiBase()).toBe('http://127.0.0.1:41234');
	});

	it('falls back to same-origin (empty string) when not injected', () => {
		expect(getApiBase()).toBe('');
	});
});
