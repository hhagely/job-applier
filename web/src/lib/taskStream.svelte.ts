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

class TaskStream {
	/** Live per-task snapshots, keyed by task id, reduced from the SSE stream.
	 *  Insertion order tracks start order, so "last of a kind" is the newest run. */
	tasks = $state<Record<string, TaskSnapshot>>({});

	#es: EventSource | null = null;
	#base: string | null = null;
	#onSettled: ((snap: TaskSnapshot) => void) | null = null;

	/** Open the one shared stream. Idempotent for a given base so repeated layout
	 *  mounts (or HMR) don't stack connections. `onSettled` fires once per task as
	 *  it leaves "running" — the layout wires it to `invalidateAll()`. */
	connect(base: string, onSettled?: (snap: TaskSnapshot) => void): void {
		if (!browser) return;
		this.#onSettled = onSettled ?? null;
		if (this.#es && this.#base === base) return;
		this.disconnect();
		this.#base = base;
		// EventSource auto-reconnects on drop; on reconnect the server replays
		// running tasks, so state re-syncs with no work here.
		const es = new EventSource(`${base}/api/ai/events`);
		es.onmessage = (e) => this.#ingest(e.data);
		this.#es = es;
	}

	disconnect(): void {
		this.#es?.close();
		this.#es = null;
		this.#base = null;
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
			this.#onSettled?.(snap);
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
