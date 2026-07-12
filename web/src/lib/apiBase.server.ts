// Server-only resolver for the FastAPI base URL. Kept out of api.ts so that
// module stays browser-safe (no $env/dynamic/private in a browser bundle).
//
// The dev launcher / packaged shell sets JOB_APPLIER_API_BASE to the loopback
// URL of the backend it spawned on a dynamic port. Falls back to the historical
// dev default so `make api` + `make web` keeps working unchanged.
import { env } from '$env/dynamic/private';

const DEFAULT_API_BASE = 'http://127.0.0.1:8000';

export function serverApiBase(): string {
	return env.JOB_APPLIER_API_BASE || DEFAULT_API_BASE;
}
