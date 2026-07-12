// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		interface PageData {
			// Browser-reachable FastAPI base, provided by the root layout load.
			apiBase?: string;
		}
		// interface PageState {}
		// interface Platform {}
	}

	interface Window {
		// Injected by the root layout so browser-only API helpers can build
		// absolute URLs to the backend on a dynamic loopback port.
		__API_BASE__?: string;
	}
}

export {};
