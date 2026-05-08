// Tiny typed client around the FastAPI backend.
// Used from +page.server.ts so requests never leave the dev server's process.
import { env } from '$env/dynamic/private';

export const API_BASE = env.JOB_APPLIER_API ?? 'http://127.0.0.1:8000';

export type FilterStatus = 'passed' | 'dropped' | 'manual';
export type ApplicationStatus =
	| 'new'
	| 'interested'
	| 'drafted'
	| 'applied'
	| 'rejected'
	| 'archived';

export interface Company {
	id: number;
	name: string;
	domain?: string | null;
	is_blocked: boolean;
	notes?: string | null;
}

export interface Score {
	score: number;
	rubric: Record<string, unknown>;
	reasoning?: string | null;
	scored_by: string;
	scored_at: string;
}

export interface Application {
	status: ApplicationStatus;
	notes?: string | null;
	applied_at?: string | null;
	updated_at: string;
}

export interface Job {
	id: number;
	source: string;
	url: string;
	title: string;
	location?: string | null;
	remote: boolean;
	employment_type?: string | null;
	posted_at?: string | null;
	ingested_at: string;
	filter_status: FilterStatus;
	filter_reason?: string | null;
	company?: Company | null;
	score?: Score | null;
	application?: Application | null;
}

export interface JobDetail extends Job {
	description: string;
}

type FetchFn = typeof fetch;

async function call<T>(fetchFn: FetchFn, path: string, init?: RequestInit): Promise<T> {
	const res = await fetchFn(`${API_BASE}${path}`, {
		...init,
		headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) }
	});
	if (!res.ok) {
		const body = await res.text();
		throw new Error(`API ${path} -> ${res.status}: ${body}`);
	}
	return res.json() as Promise<T>;
}

export const api = {
	listJobs: (
		fetchFn: FetchFn,
		params: {
			filter_status?: FilterStatus;
			status?: ApplicationStatus;
			min_score?: number;
			unscored_only?: boolean;
			limit?: number;
		} = {}
	) => {
		const q = new URLSearchParams();
		for (const [k, v] of Object.entries(params)) {
			if (v !== undefined && v !== null) q.set(k, String(v));
		}
		return call<Job[]>(fetchFn, `/api/jobs?${q.toString()}`);
	},

	getJob: (fetchFn: FetchFn, id: number) => call<JobDetail>(fetchFn, `/api/jobs/${id}`),

	setStatus: (fetchFn: FetchFn, id: number, status: ApplicationStatus, notes?: string) =>
		call<Application>(fetchFn, `/api/jobs/${id}/status`, {
			method: 'PATCH',
			body: JSON.stringify({ status, notes })
		}),

	setNotes: (fetchFn: FetchFn, id: number, notes: string) =>
		call<Application>(fetchFn, `/api/jobs/${id}/notes`, {
			method: 'POST',
			body: JSON.stringify({ notes })
		})
};
