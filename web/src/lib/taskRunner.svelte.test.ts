// Named `.svelte.test.ts` (not `.test.ts`) because the harness below needs
// `$effect.root` / `$effect`, and Svelte only compiles runes in `.svelte` and
// `.svelte.js|ts` files. Still matched by the `src/**/*.test.ts` include.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { ActionResult, SubmitFunction } from '@sveltejs/kit';

import { createTaskRunner, type TaskRunner } from './taskRunner.svelte';
import { taskStream } from './taskStream.svelte';
import type { TaskSnapshot } from '$lib/api';

// --- harness -----------------------------------------------------------------
// `createTaskRunner` must be called during component init because it registers
// `$effect`s. Outside a component that means an `$effect.root`, which owns those
// effects and hands back a dispose to unmount them. `flushSync()` runs pending
// effects synchronously, standing in for Svelte's normal render tick — so every
// test reads state the same way a mounted component would. First harness of its
// kind in this suite; copy it for other rune modules that use `$effect`.

let disposers: (() => void)[] = [];

/** Build something inside an effect root that lives until `teardown()`. */
function inRoot<T>(build: () => T): T {
	let value!: T;
	disposers.push($effect.root(() => void (value = build())));
	flushSync(); // let the module's effects run once, as a mount would
	return value;
}

function teardown() {
	for (const dispose of disposers) dispose();
	disposers = [];
}

// --- fixtures ----------------------------------------------------------------

function snap(
	over: Partial<TaskSnapshot> & Pick<TaskSnapshot, 'id' | 'kind' | 'status'>
): TaskSnapshot {
	return { total: 1, done: 0, errors: [], results: [], ...over } as TaskSnapshot;
}

/** Publish a snapshot the way the SSE reducer would, then let effects settle.
 *  Writing `tasks` directly keeps this a taskRunner test — the stream's own
 *  parsing/reduction is covered in taskStream.test.ts. */
function push(s: TaskSnapshot) {
	taskStream.tasks = { ...taskStream.tasks, [s.id]: s };
	flushSync();
}

/** Drive `use:enhance` end to end: call the submit handler, then the callback
 *  SvelteKit invokes with the action's result. */
async function submit(runner: TaskRunner, result: ActionResult) {
	const after = await runner.enhance({} as Parameters<SubmitFunction>[0]);
	if (typeof after === 'function') {
		await (after as (opts: { result: ActionResult }) => Promise<void>)({ result });
	}
	flushSync();
}

const started: ActionResult = { type: 'success', status: 200, data: { task_id: 't1' } };

beforeEach(() => {
	vi.useFakeTimers();
	taskStream.tasks = {};
	taskStream.connection = 'idle';
});

afterEach(() => {
	teardown();
	vi.useRealTimers();
});

describe('taskRunner', () => {
	it('starts idle', () => {
		const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
		expect(runner.busy).toBe(false);
		expect(runner.snap).toBeNull();
		expect(runner.error).toBeNull();
	});

	it('is busy from submit until the first snapshot, then follows the stream', async () => {
		const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));

		await submit(runner, started);
		// Optimistic: the task is live server-side but hasn't reached the stream.
		expect(runner.busy).toBe(true);
		expect(runner.snap).toBeNull();

		push(snap({ id: 't1', kind: 'ingest', status: 'running', total: 4, done: 1 }));
		expect(runner.busy).toBe(true); // now because the stream says it's running
		expect(runner.snap?.done).toBe(1);

		push(snap({ id: 't1', kind: 'ingest', status: 'done', total: 4, done: 4 }));
		expect(runner.busy).toBe(false);
		expect(runner.snap?.status).toBe('done');
	});

	it('fires onSettled once, on the running -> terminal transition', async () => {
		const settled: TaskSnapshot[] = [];
		const runner = inRoot(() =>
			createTaskRunner({ kind: 'score_pending', onSettled: (s) => void settled.push(s) })
		);

		await submit(runner, started);
		push(snap({ id: 't1', kind: 'score_pending', status: 'running' }));
		expect(settled).toHaveLength(0);

		push(snap({ id: 't1', kind: 'score_pending', status: 'done' }));
		expect(settled.map((s) => s.id)).toEqual(['t1']);

		// A repeat of the terminal snapshot is not a new transition.
		push(snap({ id: 't1', kind: 'score_pending', status: 'done' }));
		expect(settled).toHaveLength(1);
	});

	it('scopes its snapshot to `ref`, re-reading a getter ref', async () => {
		let jobId = '5';
		const runner = inRoot(() => createTaskRunner({ kind: 'draft', ref: () => jobId }));

		push(snap({ id: 'd6', kind: 'draft', status: 'running', ref: '6' }));
		expect(runner.busy).toBe(false); // someone else's job
		expect(runner.snap).toBeNull();

		push(snap({ id: 'd5', kind: 'draft', status: 'running', ref: '5' }));
		expect(runner.snap?.id).toBe('d5');
		expect(runner.busy).toBe(true);

		jobId = '6'; // the detail pane switched jobs
		expect(runner.snap?.id).toBe('d6');
	});

	it('surfaces the action error on a failed start and stops being busy', async () => {
		const runner = inRoot(() =>
			createTaskRunner({ kind: 'ingest', failMessage: 'could not start scrape' })
		);

		await submit(runner, { type: 'failure', status: 400, data: { error: 'no AI provider' } });
		expect(runner.busy).toBe(false);
		expect(runner.error).toBe('no AI provider');
	});

	it('falls back to failMessage when the failure carries no error field', async () => {
		const runner = inRoot(() =>
			createTaskRunner({ kind: 'ingest', failMessage: 'could not start scrape' })
		);
		await submit(runner, { type: 'failure', status: 500, data: {} });
		expect(runner.error).toBe('could not start scrape');
	});

	it('clears busy when a success carries no task_id', async () => {
		const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
		await submit(runner, { type: 'success', status: 200, data: {} });
		expect(runner.busy).toBe(false);
		expect(runner.error).toBeNull();
	});

	it('clears a stale error on the next submit', async () => {
		const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
		await submit(runner, { type: 'failure', status: 400, data: { error: 'boom' } });
		expect(runner.error).toBe('boom');

		await submit(runner, started);
		expect(runner.error).toBeNull();
	});

	it('dismiss drops the settled snapshot and the error', async () => {
		const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
		await submit(runner, started);
		push(snap({ id: 't1', kind: 'ingest', status: 'done' }));
		runner.setError('something went wrong');
		expect(runner.snap).not.toBeNull();

		runner.dismiss();
		flushSync();
		expect(runner.snap).toBeNull();
		expect(runner.error).toBeNull();
		expect(taskStream.tasks).toEqual({});
	});

	// --- the latch ------------------------------------------------------------
	// A dead SSE stream used to leave `busy` true forever: the start succeeded, so
	// `starting` was set, and only a snapshot could clear it. The button stayed
	// disabled with no explanation until the user reloaded the page.
	describe('when the stream never delivers a snapshot', () => {
		it('stops being busy and explains why', async () => {
			const runner = inRoot(() => createTaskRunner({ kind: 'score_pending' }));
			await submit(runner, started);
			expect(runner.busy).toBe(true);

			vi.advanceTimersByTime(8000);
			flushSync();

			expect(runner.busy).toBe(false); // the button is usable again
			expect(runner.snap).toBeNull();
			expect(runner.error).toMatch(/no progress arrived/i);
			expect(runner.error).toMatch(/reload/i);
		});

		it('blames the connection when the stream reports itself down', async () => {
			const runner = inRoot(() => createTaskRunner({ kind: 'score_pending' }));
			await submit(runner, started);
			taskStream.connection = 'closed';

			vi.advanceTimersByTime(8000);
			flushSync();

			expect(runner.busy).toBe(false);
			expect(runner.error).toMatch(/lost the connection/i);
		});

		it('does not fire once a snapshot has arrived', async () => {
			const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
			await submit(runner, started);
			push(snap({ id: 't1', kind: 'ingest', status: 'running' }));

			vi.advanceTimersByTime(60_000);
			flushSync();

			expect(runner.error).toBeNull();
			expect(runner.busy).toBe(true); // still running, per the stream
		});

		it('does not fire after the task settled', async () => {
			const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
			await submit(runner, started);
			push(snap({ id: 't1', kind: 'ingest', status: 'running' }));
			push(snap({ id: 't1', kind: 'ingest', status: 'done' }));

			vi.advanceTimersByTime(60_000);
			flushSync();

			expect(runner.error).toBeNull();
			expect(runner.busy).toBe(false);
		});

		it('does not re-error after dismiss', async () => {
			const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
			await submit(runner, started);
			runner.dismiss();

			vi.advanceTimersByTime(60_000);
			flushSync();

			expect(runner.error).toBeNull();
		});

		it('does not touch state after the component is destroyed', async () => {
			const runner = inRoot(() => createTaskRunner({ kind: 'ingest' }));
			await submit(runner, started);

			teardown(); // navigate away mid-start
			vi.advanceTimersByTime(60_000);

			expect(runner.error).toBeNull();
		});
	});
});
