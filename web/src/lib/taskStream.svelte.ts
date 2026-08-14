// App-level, event-driven task tracker. A single `EventSource` to
// `GET /api/ai/events` (opened once by the root layout) pushes every background
// task's progress; this singleton reduces those snapshots into per-task state.
//
// Because it lives above the router (module singleton, not page-local state), a
// running scrape / score / draft survives navigation: leave the dashboard while
// scoring and the progress keeps flowing, the StatusBar keeps showing it, and the
// button stays disabled from every page. Replaces the old per-page poll loop
// (taskRunner + pollTask).
import { browser } from '$app/environment';
import type { TaskSnapshot } from '$lib/api';

/** Human labels for the StatusBar, keyed by the backend task `kind`. */
const KIND_LABELS: Record<string, { running: string; done: string }> = {
	ingest: { running: 'Scraping', done: 'Scraped' },
	score_pending: { running: 'Scoring', done: 'Scored' },
	draft: { running: 'Drafting', done: 'Drafted' },
	draft_batch: { running: 'Drafting', done: 'Drafted' }
};

export function taskLabel(kind: string): { running: string; done: string } {
	return KIND_LABELS[kind] ?? { running: 'Working', done: 'Done' };
}

/** Health of the shared SSE connection.
 *  - `idle` — never connected, or disconnected.
 *  - `connecting` — opened, nothing received yet.
 *  - `open` — the server is reaching us.
 *  - `reconnecting` — the connection dropped; EventSource is retrying on its own.
 *  - `closed` — EventSource gave up. Progress will not arrive again this session. */
export type StreamConnection = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

/** `EventSource.CLOSED`, read as a literal so a stubbed/absent global still works. */
const ES_CLOSED = 2;

class TaskStream {
	/** Live per-task snapshots, keyed by task id, reduced from the SSE stream.
	 *  Insertion order tracks start order, so "last of a kind" is the newest run. */
	tasks = $state<Record<string, TaskSnapshot>>({});

	/** Observable connection health. A dead stream is otherwise invisible: tasks
	 *  keep running server-side while the UI waits forever for snapshots. */
	connection = $state<StreamConnection>('idle');

	#es: EventSource | null = null;
	#base: string | null = null;
	#onSettled: ((snap: TaskSnapshot) => void | Promise<void>) | null = null;

	/** Open the one shared stream. Idempotent for a given base so repeated layout
	 *  mounts (or HMR) don't stack connections. `onSettled` fires once per task as
	 *  it leaves "running" — the layout wires it to `invalidateAll()`. */
	connect(base: string, onSettled?: (snap: TaskSnapshot) => void | Promise<void>): void {
		if (!browser) return;
		this.#onSettled = onSettled ?? null;
		if (this.#es && this.#base === base) return;
		this.disconnect();
		this.#base = base;
		// EventSource auto-reconnects on drop; on reconnect the server replays
		// running tasks, so state re-syncs with no work here.
		const es = new EventSource(`${base}/api/ai/events`);
		es.onopen = () => (this.connection = 'open');
		es.onmessage = (e) => {
			this.connection = 'open'; // a message proves the pipe is live again
			this.#ingest(e.data);
		};
		// An error is not necessarily fatal: EventSource retries by itself and
		// only lands in CLOSED once it has given up. Either way progress is not
		// reaching us right now, which callers need to know — a task that
		// completes while we're deaf otherwise never clears its "busy" UI.
		es.onerror = () => {
			this.connection = es.readyState === ES_CLOSED ? 'closed' : 'reconnecting';
		};
		this.#es = es;
		this.connection = 'connecting';
	}

	disconnect(): void {
		this.#es?.close();
		this.#es = null;
		this.#base = null;
		this.connection = 'idle';
	}

	/** True when snapshots are not currently reaching us (retrying or given up). */
	get streamDown(): boolean {
		return this.connection === 'reconnecting' || this.connection === 'closed';
	}

	#ingest(data: string): void {
		let snap: TaskSnapshot;
		try {
			snap = JSON.parse(data) as TaskSnapshot;
		} catch {
			return; // keepalive comments never reach onmessage, but be defensive
		}
		const prev = this.tasks[snap.id];
		this.tasks = { ...this.tasks, [snap.id]: snap };
		if (snap.status !== 'running' && (!prev || prev.status === 'running')) {
			this.#notifySettled(snap);
		}
	}

	/** `onSettled` is app code (the layout's `invalidateAll()`), and it can fail —
	 *  a loader throwing while the backend is restarting, say. Contain it here so
	 *  it can neither surface as an unhandled rejection nor stop this reducer from
	 *  processing later snapshots. Callers that want the failure to be visible
	 *  catch it themselves (the layout toasts it). */
	#notifySettled(snap: TaskSnapshot): void {
		if (!this.#onSettled) return;
		try {
			void Promise.resolve(this.#onSettled(snap)).catch(() => {});
		} catch {
			/* a synchronous throw from the callback: same deal */
		}
	}

	#matches(t: TaskSnapshot, kind: string, ref?: string): boolean {
		if (t.kind !== kind) return false;
		// `ref` undefined = match any (singletons like score/ingest); otherwise the
		// task's ref must equal it (per-entity tasks like a job's draft).
		return ref == null || (t.ref ?? undefined) === ref;
	}

	/** The running task of `kind` (optionally scoped to `ref`), if one is in flight. */
	active(kind: string, ref?: string): TaskSnapshot | null {
		for (const t of Object.values(this.tasks)) {
			if (this.#matches(t, kind, ref) && t.status === 'running') return t;
		}
		return null;
	}

	isRunning(kind: string, ref?: string): boolean {
		return this.active(kind, ref) !== null;
	}

	/** Newest task of `kind` (+`ref`), running or the last settled one — for the
	 *  progress panel that lingers until dismissed. Null once dismissed. */
	latest(kind: string, ref?: string): TaskSnapshot | null {
		let best: TaskSnapshot | null = null;
		for (const t of Object.values(this.tasks)) {
			if (this.#matches(t, kind, ref)) best = t;
		}
		return best;
	}

	/** First running task of any kind — drives the global StatusBar indicator. */
	get running(): TaskSnapshot | null {
		for (const t of Object.values(this.tasks)) {
			if (t.status === 'running') return t;
		}
		return null;
	}

	/** Drop a settled task (the panel "Dismiss" button). Running tasks aren't
	 *  dismissable in the UI, so this only clears terminal snapshots. */
	dismiss(id: string): void {
		if (!(id in this.tasks)) return;
		const { [id]: _drop, ...rest } = this.tasks;
		this.tasks = rest;
	}
}

export const taskStream = new TaskStream();
