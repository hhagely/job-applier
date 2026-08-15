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

/** How long a successful start may go without its first stream snapshot before
 *  we stop believing one is coming. Generous enough for a slow first snapshot,
 *  short enough that a dead stream doesn't disable the button all session. */
const FIRST_SNAPSHOT_TIMEOUT_MS = 8000;

export function createTaskRunner(opts: TaskRunnerOptions): TaskRunner {
	let starting = $state(false);
	let error = $state<string | null>(null);
	let startTimer: ReturnType<typeof setTimeout> | null = null;

	function currentRef(): string | undefined {
		return typeof opts.ref === 'function' ? opts.ref() : opts.ref;
	}

	function clearStartTimer(): void {
		if (startTimer === null) return;
		clearTimeout(startTimer);
		startTimer = null;
	}

	// The task started fine, so `starting` waits for the stream to confirm it. If
	// the stream is dead (backend restart, proxy reap, EventSource out of retries)
	// that confirmation never comes and `busy` used to latch true until a full
	// reload. Time it out instead and say so: the task itself is still running
	// server-side, we just stopped being able to watch it.
	function armStartTimer(): void {
		clearStartTimer();
		startTimer = setTimeout(() => {
			startTimer = null;
			if (!starting) return;
			starting = false;
			error = taskStream.streamDown
				? 'Lost the connection to the app server. The task is probably still running — reload to see its result.'
				: 'Started, but no progress arrived. The task is probably still running — reload to see its result.';
		}, FIRST_SNAPSHOT_TIMEOUT_MS);
	}

	// Clear the optimistic "starting" flag once the stream has picked up the task,
	// and fire the local onSettled on the running -> terminal transition.
	let lastStatus: TaskSnapshot['status'] | undefined;
	$effect(() => {
		const snap = taskStream.latest(opts.kind, currentRef());
		if (starting && snap) {
			starting = false;
			clearStartTimer();
		}
		const status = snap?.status;
		if (status && status !== 'running' && lastStatus === 'running') {
			void opts.onSettled?.(snap!);
		}
		lastStatus = status;
	});

	// Reads nothing reactive, so it runs once: its teardown is just "drop the
	// pending timer when the owning component goes away". Kept out of the effect
	// above, whose teardown would fire on every re-run and disarm a live timer.
	$effect(() => clearStartTimer);

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
			clearStartTimer();
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
					// once its first snapshot arrives (no poll, no flicker) — and the
					// timer clears it if the stream never delivers one.
					armStartTimer();
					return;
				}
				starting = false;
				clearStartTimer();
				if (result.type === 'failure') {
					error = (result.data?.error as string) ?? opts.failMessage ?? 'action failed';
				}
			};
		}
	};
}
