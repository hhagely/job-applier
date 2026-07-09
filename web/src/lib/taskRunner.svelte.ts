// Client-side lifecycle for an in-process background task (scrape / score /
// draft). Wraps the repeated `starting`/`polling`/`snapshot`/`error` state, the
// `use:enhance` submit handler that reads the `{ task_id }` an action returns,
// and the poll loop. Callers own what happens when the task settles via
// `onSettled`. Lives in a `.svelte.ts` module so it can hold `$state`.
import { pollTask } from '$lib/pollTask';
import type { TaskSnapshot } from '$lib/api';
import type { SubmitFunction } from '@sveltejs/kit';

export interface TaskRunnerOptions {
	/** Base URL for the poll endpoint. Pass a getter (e.g. `() => data.apiBase ?? ''`)
	 *  so the reactive value is read when the task starts, not captured at init. */
	apiBase: string | (() => string);
	/** Ran once the task reaches a terminal state (done or error) with the final
	 *  snapshot — invalidate, clear a cart, reload a draft, etc. Not called if the
	 *  poll itself throws (that surfaces as `error`). */
	onSettled?: (snap: TaskSnapshot) => void | Promise<void>;
	/** Fallback message when the start action fails without an `error` field. */
	failMessage?: string;
}

export interface TaskRunner {
	/** Latest snapshot, or null before a run / after dismiss. */
	readonly snap: TaskSnapshot | null;
	readonly error: string | null;
	/** True from submit until the task settles — for disabling/spinner copy. */
	readonly busy: boolean;
	/** Drop-in `use:enhance` handler: starts the task and begins polling. */
	enhance: SubmitFunction;
	/** Clear the snapshot + error (the panel "Dismiss" button). */
	dismiss(): void;
	/** Set the error slot directly — for sibling forms that share this panel
	 *  (e.g. a synchronous "re-render" that isn't itself a polled task). */
	setError(message: string | null): void;
}

export function createTaskRunner(opts: TaskRunnerOptions): TaskRunner {
	let snap = $state<TaskSnapshot | null>(null);
	let starting = $state(false);
	let polling = $state(false);
	let error = $state<string | null>(null);

	async function poll(taskId: string) {
		polling = true;
		try {
			const base = typeof opts.apiBase === 'function' ? opts.apiBase() : opts.apiBase;
			const final = await pollTask(fetch, base, taskId, (s) => (snap = s));
			await opts.onSettled?.(final);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			polling = false;
		}
	}

	return {
		get snap() {
			return snap;
		},
		get error() {
			return error;
		},
		get busy() {
			return starting || polling;
		},
		dismiss() {
			snap = null;
			error = null;
		},
		setError(message: string | null) {
			error = message;
		},
		enhance: () => {
			starting = true;
			error = null;
			return async ({ result }) => {
				starting = false;
				if (result.type === 'success' && result.data?.task_id) {
					snap = null;
					poll(result.data.task_id as string);
				} else if (result.type === 'failure') {
					error = (result.data?.error as string) ?? opts.failMessage ?? 'action failed';
				}
			};
		}
	};
}
