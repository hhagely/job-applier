import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TaskSnapshot } from '$lib/api';

// The store gates connect() on browser and opens an EventSource; force browser
// on and stub EventSource so the test can push snapshots through onmessage.
vi.mock('$app/environment', () => ({ browser: true }));

let lastES: FakeEventSource | null = null;

class FakeEventSource {
	onmessage: ((e: { data: string }) => void) | null = null;
	url: string;
	closed = false;
	constructor(url: string) {
		this.url = url;
		lastES = this;
	}
	close() {
		this.closed = true;
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
		taskStream.connect('http://x', (s) => settled.push(s.id));

		push(snap({ id: 't1', kind: 'score_pending', status: 'running' }));
		push(snap({ id: 't1', kind: 'score_pending', status: 'done', done: 1 }));

		expect(settled).toEqual(['t1']);
		expect(taskStream.isRunning('score_pending')).toBe(false);
		// The settled snapshot lingers as `latest` until dismissed.
		expect(taskStream.latest('score_pending')?.status).toBe('done');
	});

	it('does not re-fire onSettled for an already-settled task', () => {
		const settled: string[] = [];
		taskStream.connect('http://x', (s) => settled.push(s.id));
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

	it('ignores malformed stream data without throwing', () => {
		taskStream.connect('http://x');
		expect(() => lastES?.onmessage?.({ data: 'not json' })).not.toThrow();
		expect(taskStream.running).toBeNull();
	});
});

describe('taskLabel', () => {
	it('maps known kinds and falls back for unknown ones', () => {
		expect(taskLabel('ingest')).toEqual({ running: 'Scraping', done: 'Scraped' });
		expect(taskLabel('mystery')).toEqual({ running: 'Working', done: 'Done' });
	});
});
