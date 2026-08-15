import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TaskSnapshot } from '$lib/api';

// The store gates connect() on browser and opens an EventSource; force browser
// on and stub EventSource so the test can push snapshots through onmessage.
vi.mock('$app/environment', () => ({ browser: true }));

let lastES: FakeEventSource | null = null;

class FakeEventSource {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSED = 2;

	onmessage: ((e: { data: string }) => void) | null = null;
	onopen: (() => void) | null = null;
	onerror: (() => void) | null = null;
	url: string;
	closed = false;
	/** Mirrors the real `EventSource.readyState`; the store reads it to tell a
	 *  retrying connection from one that has given up. */
	readyState = FakeEventSource.CONNECTING;
	constructor(url: string) {
		this.url = url;
		lastES = this;
	}
	close() {
		this.closed = true;
		this.readyState = FakeEventSource.CLOSED;
	}
	/** The browser's own reconnect loop: error, still CONNECTING, will retry. */
	dropAndRetry() {
		this.readyState = FakeEventSource.CONNECTING;
		this.onerror?.();
	}
	/** The browser giving up for good. */
	giveUp() {
		this.readyState = FakeEventSource.CLOSED;
		this.onerror?.();
	}
	open() {
		this.readyState = FakeEventSource.OPEN;
		this.onopen?.();
	}
}

vi.stubGlobal('EventSource', FakeEventSource);

import { taskStream, taskLabel } from './taskStream.svelte';

function snap(over: Partial<TaskSnapshot> & Pick<TaskSnapshot, 'id' | 'kind' | 'status'>): TaskSnapshot {
	return { total: 1, done: 0, errors: [], results: [], ...over } as TaskSnapshot;
}

/** Feed a snapshot as if it arrived on the SSE stream. */
function push(s: TaskSnapshot) {
	lastES?.onmessage?.({ data: JSON.stringify(s) });
}

beforeEach(() => {
	for (const id of Object.keys(taskStream.tasks)) taskStream.dismiss(id);
	taskStream.disconnect();
	lastES = null;
});

describe('taskStream store', () => {
	it('reduces a running snapshot into active/isRunning/running/latest', () => {
		taskStream.connect('http://x');
		push(snap({ id: 't1', kind: 'ingest', status: 'running' }));

		expect(taskStream.isRunning('ingest')).toBe(true);
		expect(taskStream.active('ingest')?.id).toBe('t1');
		expect(taskStream.running?.id).toBe('t1');
		expect(taskStream.latest('ingest')?.id).toBe('t1');
	});

	it('fires onSettled once as a task leaves running, and clears isRunning', () => {
		const settled: string[] = [];
		taskStream.connect('http://x', (s) => void settled.push(s.id));

		push(snap({ id: 't1', kind: 'score_pending', status: 'running' }));
		push(snap({ id: 't1', kind: 'score_pending', status: 'done', done: 1 }));

		expect(settled).toEqual(['t1']);
		expect(taskStream.isRunning('score_pending')).toBe(false);
		// The settled snapshot lingers as `latest` until dismissed.
		expect(taskStream.latest('score_pending')?.status).toBe('done');
	});

	it('does not re-fire onSettled for an already-settled task', () => {
		const settled: string[] = [];
		taskStream.connect('http://x', (s) => void settled.push(s.id));
		push(snap({ id: 't1', kind: 'draft', status: 'running' }));
		push(snap({ id: 't1', kind: 'draft', status: 'done' })); // running -> done: fires
		push(snap({ id: 't1', kind: 'draft', status: 'done' })); // done -> done: no re-fire
		expect(settled).toEqual(['t1']);
	});

	it('scopes active/latest by ref for per-entity tasks', () => {
		taskStream.connect('http://x');
		push(snap({ id: 'd5', kind: 'draft', status: 'running', ref: '5' }));
		push(snap({ id: 'd6', kind: 'draft', status: 'running', ref: '6' }));

		expect(taskStream.active('draft', '5')?.id).toBe('d5');
		expect(taskStream.active('draft', '6')?.id).toBe('d6');
		expect(taskStream.isRunning('draft', '7')).toBe(false);
	});

	it('dismiss removes a task from state', () => {
		taskStream.connect('http://x');
		push(snap({ id: 't1', kind: 'ingest', status: 'done' }));
		expect(taskStream.latest('ingest')).not.toBeNull();
		taskStream.dismiss('t1');
		expect(taskStream.latest('ingest')).toBeNull();
	});

	it('ignores malformed stream data and keeps reducing later snapshots', () => {
		taskStream.connect('http://x');
		push(snap({ id: 't1', kind: 'ingest', status: 'running' }));

		lastES?.onmessage?.({ data: 'not json' });
		expect(taskStream.running?.id).toBe('t1'); // the bad frame changed nothing

		push(snap({ id: 't1', kind: 'ingest', status: 'done' }));
		expect(taskStream.isRunning('ingest')).toBe(false);
	});

	// A callback that rejects (invalidateAll() while the backend restarts) must
	// not escape as an unhandled rejection — vitest fails the run on those — nor
	// stop the reducer.
	it('survives an onSettled callback that rejects', async () => {
		taskStream.connect('http://x', () => Promise.reject(new Error('loader failed')));
		push(snap({ id: 't1', kind: 'ingest', status: 'running' }));
		push(snap({ id: 't1', kind: 'ingest', status: 'done' }));
		await Promise.resolve();
		await Promise.resolve();

		push(snap({ id: 't2', kind: 'draft', status: 'running' }));
		expect(taskStream.isRunning('draft')).toBe(true);
	});

	it('survives an onSettled callback that throws synchronously', () => {
		taskStream.connect('http://x', () => {
			throw new Error('boom');
		});
		push(snap({ id: 't1', kind: 'ingest', status: 'running' }));
		push(snap({ id: 't1', kind: 'ingest', status: 'done' }));

		expect(taskStream.latest('ingest')?.status).toBe('done');
	});
});

// Without this the UI cannot tell "nothing is running" from "we stopped hearing
// about what is running", which is how a task button ends up disabled forever.
describe('taskStream connection health', () => {
	it('is idle before connect and after disconnect', () => {
		expect(taskStream.connection).toBe('idle');
		taskStream.connect('http://x');
		lastES?.open();
		expect(taskStream.connection).toBe('open');
		taskStream.disconnect();
		expect(taskStream.connection).toBe('idle');
		expect(taskStream.streamDown).toBe(false);
	});

	it('reports reconnecting while EventSource retries, then open on the next message', () => {
		taskStream.connect('http://x');
		lastES?.open();

		lastES?.dropAndRetry();
		expect(taskStream.connection).toBe('reconnecting');
		expect(taskStream.streamDown).toBe(true);

		// The server replays running tasks on reconnect; that first frame is proof
		// the pipe is live again.
		push(snap({ id: 't1', kind: 'ingest', status: 'running' }));
		expect(taskStream.connection).toBe('open');
		expect(taskStream.streamDown).toBe(false);
	});

	it('reports closed once EventSource gives up', () => {
		taskStream.connect('http://x');
		lastES?.open();
		lastES?.giveUp();
		expect(taskStream.connection).toBe('closed');
		expect(taskStream.streamDown).toBe(true);
	});

	it('starts out connecting, before anything has been received', () => {
		taskStream.connect('http://x');
		expect(taskStream.connection).toBe('connecting');
		expect(taskStream.streamDown).toBe(false);
	});
});

describe('taskLabel', () => {
	it('maps known kinds and falls back for unknown ones', () => {
		expect(taskLabel('ingest')).toEqual({ running: 'Scraping', done: 'Scraped' });
		expect(taskLabel('mystery')).toEqual({ running: 'Working', done: 'Done' });
	});
});
