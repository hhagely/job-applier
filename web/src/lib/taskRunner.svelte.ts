// Client-side glue for starting + tracking one background task (scrape / score /
// draft), backed by the shared event stream (`taskStream`) instead of a poll
// loop. The `use:enhance` handler starts the task via its form action; progress
// then arrives over the app-wide SSE connection, so it survives navigation and is
// visible from the StatusBar. Callers read `snap` / `busy` for their UI.
//
// Lives in a `.svelte.ts` module so it can hold `$state` / `$effect`. It must be
// created during component init (like `$state`) so its settle-effect binds to the
// calling component.
import { taskStream } from '$lib/taskStream.svelte';
import type { TaskSnapshot } from '$lib/api';
import type { SubmitFunction } from '@sveltejs/kit';

export interface TaskRunnerOptions {
	/** Backend task `kind` this runner starts and tracks (e.g. `score_pending`). */
	kind: string;
	/** Optional per-kind discriminator (e.g. a job id) so a per-entity task is
	 *  tracked distinctly from others of the same kind. Pass a getter when it comes
	 *  from a prop that can change. */
	ref?: string | (() => string | undefined);
	/** Ran when THIS runner's task settles. The layout already does a global
	 *  `invalidateAll()` on every settle, so only pass this to refresh state a page
	 *  loader does NOT own (e.g. the Queue's lazily-fetched draft). */
	onSettled?: (snap: TaskSnapshot) => void | Promise<void>;
	/** Fallback message when the start action fails without an `error` field. */
	failMessage?: string;
}

export interface TaskRunner {
	/** Latest snapshot for this runner's task, or null before a run / after dismiss. */
	readonly snap: TaskSnapshot | null;
	readonly error: string | null;
	/** True from submit until the task settles — for disabling/spinner copy. */
	readonly busy: boolean;
	/** Drop-in `use:enhance` handler: starts the task; the stream takes it from there. */
	enhance: SubmitFunction;
	/** Clear this runner's settled task + error (the panel "Dismiss" button). */
	dismiss(): void;
	/** Set the error slot directly — for sibling forms that share this panel
	 *  (e.g. a synchronous "re-render" that isn't itself a tracked task). */
	setError(message: string | null): void;
}

export function createTaskRunner(opts: TaskRunnerOptions): TaskRunner {
	let starting = $state(false);
	let error = $state<string | null>(null);

	function currentRef(): string | undefined {
		return typeof opts.ref === 'function' ? opts.ref() : opts.ref;
	}

	// Clear the optimistic "starting" flag once the stream has picked up the task,
	// and fire the local onSettled on the running -> terminal transition.
	let lastStatus: TaskSnapshot['status'] | undefined;
	$effect(() => {
		const snap = taskStream.latest(opts.kind, currentRef());
		if (starting && snap) starting = false;
		const status = snap?.status;
		if (status && status !== 'running' && lastStatus === 'running') {
			void opts.onSettled?.(snap!);
		}
		lastStatus = status;
	});

	return {
		get snap() {
			return taskStream.latest(opts.kind, currentRef());
		},
		get error() {
			return error;
		},
		get busy() {
			return starting || taskStream.isRunning(opts.kind, currentRef());
		},
		dismiss() {
			const snap = taskStream.latest(opts.kind, currentRef());
			if (snap) taskStream.dismiss(snap.id);
			error = null;
		},
		setError(message: string | null) {
			error = message;
		},
		enhance: () => {
			starting = true;
			error = null;
			return async ({ result }) => {
				if (result.type === 'success' && result.data?.task_id) {
					// Task is live on the stream now; the $effect clears `starting`
					// once its first snapshot arrives (no poll, no flicker).
					return;
				}
				starting = false;
				if (result.type === 'failure') {
					error = (result.data?.error as string) ?? opts.failMessage ?? 'action failed';
				}
			};
		}
	};
}
