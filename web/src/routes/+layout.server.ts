import { serverApiBase } from '$lib/apiBase.server';
import type { LayoutServerLoad } from './$types';

// Expose the browser-reachable API base to the client. It is injected into the
// page as `window.__API_BASE__` (see +layout.svelte) so browser-only helpers
// (PDF download links) can build absolute URLs, and is available to every page
// via `page.data.apiBase`. Same loopback URL the server-side loaders use — it is
// reachable from the browser on the same machine.
export const load: LayoutServerLoad = () => {
	return { apiBase: serverApiBase() };
};
