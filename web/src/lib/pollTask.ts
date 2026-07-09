import { api, type TaskSnapshot } from '$lib/api';

/**
 * Poll `GET /api/ai/tasks/{id}` once per `intervalMs` until the background task
 * leaves the "running" state, invoking `onSnapshot` with every snapshot
 * (including the terminal one). Resolves with the final snapshot and throws on a
 * fetch error so callers can surface it. Callers own what happens next
 * (`invalidateAll`, reloading a draft, etc.).
 */
export async function pollTask(
	fetchFn: typeof fetch,
	base: string,
	taskId: string,
	onSnapshot: (snap: TaskSnapshot) => void,
	intervalMs = 1000
): Promise<TaskSnapshot> {
	// eslint-disable-next-line no-constant-condition
	while (true) {
		const snap = await api.getTask(fetchFn, base, taskId);
		onSnapshot(snap);
		if (snap.status !== 'running') return snap;
		await new Promise((r) => setTimeout(r, intervalMs));
	}
}
