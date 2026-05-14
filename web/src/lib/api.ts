// Tiny typed client around the FastAPI backend.
// Imported by both +page.server.ts (server-side fetches via form actions)
// and +page.svelte (browser-side helpers like resumePdfUrl()), so this module
// must stay browser-safe — no $env/dynamic/private here.

export const API_BASE = 'http://127.0.0.1:8000';

export type FilterStatus = 'passed' | 'manual';
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
	resume_id?: number | null;
	resume_filename?: string | null;
	score_kind: 'baseline' | 'tailored';
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

export interface Draft {
	job_id: number;
	has_resume_md: boolean;
	has_resume_pdf: boolean;
	has_cover_letter_md: boolean;
	has_cover_letter_pdf: boolean;
	updated_at?: string | null;
	resume_md?: string | null;
	cover_letter_md?: string | null;
}

export interface Resume {
	id: number;
	original_filename: string;
	page_count?: number | null;
	is_active: boolean;
	uploaded_at: string;
	extracted_text: string;
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

async function callOptional<T>(
	fetchFn: FetchFn,
	path: string,
	init?: RequestInit
): Promise<T | null> {
	const res = await fetchFn(`${API_BASE}${path}`, init);
	if (res.status === 404) return null;
	if (!res.ok) throw new Error(`API ${path} -> ${res.status}: ${await res.text()}`);
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

	getScoreHistory: (fetchFn: FetchFn, jobId: number) =>
		call<Score[]>(fetchFn, `/api/jobs/${jobId}/score-history`),

	setStatus: (fetchFn: FetchFn, id: number, status: ApplicationStatus, notes?: string) =>
		call<Application>(fetchFn, `/api/jobs/${id}/status`, {
			method: 'PATCH',
			body: JSON.stringify({ status, notes })
		}),

	bulkSetStatus: (fetchFn: FetchFn, job_ids: number[], status: ApplicationStatus) =>
		call<Application[]>(fetchFn, `/api/jobs/bulk-status`, {
			method: 'POST',
			body: JSON.stringify({ job_ids, status })
		}),

	setNotes: (fetchFn: FetchFn, id: number, notes: string) =>
		call<Application>(fetchFn, `/api/jobs/${id}/notes`, {
			method: 'POST',
			body: JSON.stringify({ notes })
		}),

	getCurrentResume: (fetchFn: FetchFn) => callOptional<Resume>(fetchFn, '/api/resume/current'),

	uploadResume: async (fetchFn: FetchFn, file: File): Promise<Resume> => {
		const fd = new FormData();
		fd.append('file', file, file.name);
		const res = await fetchFn(`${API_BASE}/api/resume`, { method: 'POST', body: fd });
		if (!res.ok) throw new Error(`upload failed: ${res.status} ${await res.text()}`);
		return res.json() as Promise<Resume>;
	},

	resumePdfUrl: () => `${API_BASE}/api/resume/current/pdf`,

	getDraft: (fetchFn: FetchFn, jobId: number, includeMarkdown = false) =>
		callOptional<Draft>(
			fetchFn,
			`/api/jobs/${jobId}/draft${includeMarkdown ? '?include_markdown=true' : ''}`
		),

	renderDraft: (fetchFn: FetchFn, jobId: number) =>
		call<Draft>(fetchFn, `/api/jobs/${jobId}/draft/render`, { method: 'POST' }),

	draftResumePdfUrl: (jobId: number) => `${API_BASE}/api/jobs/${jobId}/draft/resume.pdf`,

	draftCoverLetterPdfUrl: (jobId: number) =>
		`${API_BASE}/api/jobs/${jobId}/draft/cover-letter.pdf`
};
