import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, errorReason, getApiBase } from './api';

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

	it("attaches FastAPI's `detail` so the UI can show a sentence, not a status line", async () => {
		// `message` stays the full form for logs; `detail` is what a form action
		// surfaces to the user (jobActions.server reads it structurally).
		const fetchFn = vi.fn().mockResolvedValue(
			jsonResponse({ detail: 'The database is busy.' }, { status: 503 })
		);
		const err = await api.listJobs(fetchFn, TEST_BASE).catch((e) => e);
		expect(err.detail).toBe('The database is busy.');
		expect(err.status).toBe(503);
		expect(err.message).toMatch(/503/);
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

describe('errorReason', () => {
	it("prefers FastAPI's detail over the raw `API <path> -> <status>` envelope", async () => {
		const fetchFn = vi
			.fn()
			.mockResolvedValue(jsonResponse({ detail: 'The database is busy.' }, { status: 503 }));
		const err = await api.listJobs(fetchFn, TEST_BASE).catch((e) => e);
		expect(errorReason(err)).toBe('The database is busy.');
	});

	it('handles a detail containing quotes, and falls back when there is no detail', async () => {
		// Quotes are why this reads the parsed `detail` field: the regex this
		// replaced scraped `{"detail":"..."}` out of the message text and handed
		// back the JSON-escaped form.
		const fetchFn = vi
			.fn()
			.mockResolvedValue(jsonResponse({ detail: 'Unknown state "MO, Missouri".' }, { status: 422 }));
		const err = await api.listJobs(fetchFn, TEST_BASE).catch((e) => e);
		expect(errorReason(err)).toBe('Unknown state "MO, Missouri".');

		expect(errorReason(new Error('connect ECONNREFUSED'))).toBe('connect ECONNREFUSED');
		expect(errorReason(null)).toBe('request failed');
	});
});

// The retry policy is what keeps a dropped socket from replaying a mutation, so
// it gets pinned here. Every wait below is a fake timer — nothing in this block
// sleeps on the wall clock.
describe('fetchWithRetry policy', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	/** What fetch() does when no HTTP response is ever produced. */
	const netFail = () => new TypeError('fetch failed');

	it('retries a GET that never got a response, after backing off', async () => {
		const fetchFn = vi
			.fn()
			.mockRejectedValueOnce(netFail())
			.mockResolvedValueOnce(jsonResponse([{ id: 1 }]));

		const pending = api.listJobs(fetchFn, TEST_BASE);
		await vi.advanceTimersByTimeAsync(0); // let the first rejection settle
		expect(fetchFn).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(249); // the first backoff is 250ms
		expect(fetchFn).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(1);
		expect(fetchFn).toHaveBeenCalledTimes(2);
		await expect(pending).resolves.toEqual([{ id: 1 }]);
	});

	it('does not retry a PATCH — it may have been applied before the socket dropped', async () => {
		const fetchFn = vi.fn().mockRejectedValue(netFail());
		const pending = api.setStatus(fetchFn, TEST_BASE, 7, 'applied');
		const settled = expect(pending).rejects.toThrow('fetch failed');
		await vi.runAllTimersAsync();
		await settled;
		expect(fetchFn).toHaveBeenCalledTimes(1);
	});

	it('does not retry the DELETE behind removeBlacklist', async () => {
		const fetchFn = vi.fn().mockRejectedValue(netFail());
		const pending = api.removeBlacklist(fetchFn, TEST_BASE, 7);
		const settled = expect(pending).rejects.toThrow('fetch failed');
		await vi.runAllTimersAsync();
		await settled;
		expect(fetchFn).toHaveBeenCalledTimes(1);
	});

	it('gives up on a GET after the configured attempts and rethrows the last error', async () => {
		const fetchFn = vi.fn().mockRejectedValue(netFail());
		const pending = api.listJobs(fetchFn, TEST_BASE);
		const settled = expect(pending).rejects.toThrow('fetch failed');
		await vi.runAllTimersAsync();
		await settled;
		// The initial attempt plus one per configured backoff delay.
		expect(fetchFn).toHaveBeenCalledTimes(5);
	});

	it('never retries a response it received, even a 5xx', async () => {
		// The server produced the response, so the request *was* processed —
		// replaying it would be a second write, not a recovery.
		const fetchFn = vi.fn().mockResolvedValue(new Response('busy', { status: 503 }));
		await expect(api.listJobs(fetchFn, TEST_BASE)).rejects.toThrow(/503/);
		expect(fetchFn).toHaveBeenCalledTimes(1);
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
