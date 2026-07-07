import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import type { LayoutServerLoad } from './$types';

// Expose the browser-reachable API base to the client. It is injected into the
// page as `window.__API_BASE__` (see +layout.svelte) so browser-only helpers
// (PDF download links) can build absolute URLs, and is available to every page
// via `page.data.apiBase`. Same loopback URL the server-side loaders use — it is
// reachable from the browser on the same machine.
//
// Also surface the selected AI provider for the header indicator. This is a
// cheap settings read (no CLI detection) and is defensive: any failure just
// shows "AI: none" rather than breaking every page.
export const load: LayoutServerLoad = async ({ fetch }) => {
	const base = serverApiBase();
	let aiProvider: string | null = null;
	try {
		aiProvider = (await api.getSelectedProvider(fetch, base)).selected;
	} catch {
		aiProvider = null;
	}
	return { apiBase: base, aiProvider };
};
