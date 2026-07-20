// Tiny typed client around the FastAPI backend.
// Imported by both +page.server.ts (server-side fetches via form actions)
// and +page.svelte (browser-side helpers like resumePdfUrl()), so this module
// must stay browser-safe — no $env/dynamic/private here.
//
// The API base is a per-call parameter, not a module constant, because the same
// module is bundled for both the SvelteKit server and the browser and the base
// differs (and is dynamic in the packaged app / dev launcher):
//   - server callers pass the base from `serverApiBase()` (apiBase.server.ts,
//     reads JOB_APPLIER_API_BASE),
//   - browser callers pass `getApiBase()` (reads the injected window.__API_BASE__)
//     or the `apiBase` value threaded through page data.

/**
 * Browser-side API base, injected by the root layout as `window.__API_BASE__`.
 * Falls back to same-origin (empty string -> relative `/api/...`) when unset,
 * e.g. during SSR where `window` is absent.
 */
export function getApiBase(): string {
	if (typeof window !== 'undefined') {
		const injected = (window as unknown as { __API_BASE__?: string }).__API_BASE__;
		if (injected) return injected;
	}
	return '';
}

// `dropped` is intentionally omitted from the client type: dropped jobs are
// never persisted, so the API never emits them. Keep in sync with FilterStatus
// in models/db.py, which does include `dropped`.
export type FilterStatus = 'passed' | 'manual';
export type ApplicationStatus =
	| 'new'
	| 'interested'
	| 'drafted'
	| 'applied'
	| 'screening'
	| 'interviewing'
	| 'rejected'
	| 'archived';

/**
 * All application statuses, in pipeline order — the single client-side source for
 * validation guards and status dropdowns. Keep in sync with the ApplicationStatus
 * enum in models/db.py (no shared codegen across the Python/TS boundary).
 */
export const APPLICATION_STATUSES: ApplicationStatus[] = [
	'new',
	'interested',
	'drafted',
	'applied',
	'screening',
	'interviewing',
	'rejected',
	'archived'
];

export interface Company {
	id: number;
	name: string;
	domain?: string | null;
	is_blocked: boolean;
	notes?: string | null;
}

export interface BlacklistedCompany {
	id: number;
	name: string;
	normalized_name: string;
	reason?: string | null;
	created_at: string;
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
	is_stale: boolean;
}

export interface Application {
	status: ApplicationStatus;
	notes?: string | null;
	applied_at?: string | null;
	updated_at: string;
	next_followup_at?: string | null;
	last_contact_at?: string | null;
	outcome?: string | null;
	used_for_unemployment: boolean;
	used_for_unemployment_at?: string | null;
}

export interface FollowupPayload {
	next_followup_at?: string | null;
	last_contact_at?: string | null;
	outcome?: string | null;
}

export interface StatusPayload {
	notes?: string;
	next_followup_at?: string | null;
	last_contact_at?: string | null;
	outcome?: string | null;
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
	duplicate_of?: number | null;
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

export interface SearchProfile {
	id: number | null;
	role_titles: string[];
	seniority_terms: string[];
	required_tech: string[];
	excluded_tech: string[];
	extracted_skills: string[];
	/** Canonical full name of the user's state of residence, or null. Ingest-filter only. */
	home_state: string | null;
	recommendations_draft: SearchProfileRecommendation | null;
	updated_at: string | null;
	using_defaults: boolean;
}

export interface SearchProfileRecommendation {
	role_titles: string[];
	seniority_terms: string[];
	required_tech: string[];
	excluded_tech: string[];
	extracted_skills: string[];
	rationale?: string | null;
}

export interface SearchProfileBody {
	role_titles: string[];
	seniority_terms: string[];
	required_tech: string[];
	excluded_tech: string[];
	extracted_skills: string[];
	/** Full state name (e.g. "Missouri") or null/"" to leave the rule off. */
	home_state?: string | null;
}

export interface Resume {
	id: number;
	original_filename: string;
	page_count?: number | null;
	is_active: boolean;
	uploaded_at: string;
	extracted_text: string;
}

export type ProviderTier = 'recommended' | 'best-effort';

export interface Provider {
	name: string;
	display_name: string;
	tier: ProviderTier;
	available: boolean;
	version?: string | null;
	/** Baseline-scoring model choices for this provider (empty = offer free text). */
	scoring_models: ModelOption[];
	/** This provider's built-in scoring-model default, shown as the "Default" label. */
	scoring_model_default?: string | null;
}

export interface ModelOption {
	value: string;
	label: string;
}

export interface ProvidersResponse {
	providers: Provider[];
	selected?: string | null;
	model?: string | null;
	/** Persisted baseline-scoring model override (blank = use the provider default). */
	scoring_model?: string | null;
	/** The selected provider's built-in scoring-model default, shown as a placeholder. */
	scoring_model_default?: string | null;
}

export interface AiTestResult {
	ok: boolean;
	output?: string | null;
	error?: string | null;
}

export interface TaskSnapshot {
	id: string;
	kind: string;
	total: number;
	done: number;
	status: 'running' | 'done' | 'error';
	errors: string[];
	results: string[];
	/** Optional per-kind discriminator (e.g. a job id for a tailored-draft run). */
	ref?: string | null;
}

export interface UpdateInfo {
	/** The running version (backend __version__). */
	current: string;
	/** Latest GitHub Release tag, or null if the check failed / none published. */
	latest: string | null;
	update_available: boolean;
	/** Releases page to open externally. */
	url: string;
}

type FetchFn = typeof fetch;

// A backend that's momentarily unreachable — e.g. uvicorn --reload restarting
// after a Python edit in dev — makes fetch() REJECT: no HTTP response is ever
// produced. Retry those network-level rejections so page loaders and client
// polling ride through the ~1-3s reload window instead of surfacing a hard
// error page. Two guards keep replaying safe:
//   - only idempotent requests (GET/HEAD) are retried; a mutation could have
//     been received and applied just before the socket dropped, so replaying it
//     might double-apply.
//   - a received response is never retried, even a 5xx — the server produced
//     it, so the request was processed.
const RETRY_DELAYS_MS = [250, 500, 1000, 2000];

function isRetryable(init?: RequestInit): boolean {
	const method = (init?.method ?? 'GET').toUpperCase();
	return method === 'GET' || method === 'HEAD';
}

async function fetchWithRetry(fetchFn: FetchFn, url: string, init?: RequestInit): Promise<Response> {
	let lastErr: unknown;
	for (let attempt = 0; ; attempt++) {
		try {
			return await fetchFn(url, init);
		} catch (err) {
			lastErr = err;
			if (!isRetryable(init) || attempt >= RETRY_DELAYS_MS.length) break;
			await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
		}
	}
	throw lastErr;
}

async function call<T>(
	fetchFn: FetchFn,
	base: string,
	path: string,
	init?: RequestInit
): Promise<T> {
	const res = await fetchWithRetry(fetchFn, `${base}${path}`, {
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
	base: string,
	path: string,
	init?: RequestInit
): Promise<T | null> {
	const res = await fetchWithRetry(fetchFn, `${base}${path}`, init);
	if (res.status === 404) return null;
	if (!res.ok) throw new Error(`API ${path} -> ${res.status}: ${await res.text()}`);
	return res.json() as Promise<T>;
}

export const api = {
	listJobs: (
		fetchFn: FetchFn,
		base: string,
		params: {
			filter_status?: FilterStatus;
			status?: ApplicationStatus;
			min_score?: number;
			unscored_only?: boolean;
			include_duplicates?: boolean;
			limit?: number;
		} = {}
	) => {
		const q = new URLSearchParams();
		for (const [k, v] of Object.entries(params)) {
			if (v !== undefined && v !== null) q.set(k, String(v));
		}
		return call<Job[]>(fetchFn, base, `/api/jobs?${q.toString()}`);
	},

	getJob: (fetchFn: FetchFn, base: string, id: number) =>
		call<JobDetail>(fetchFn, base, `/api/jobs/${id}`),

	getScoreHistory: (fetchFn: FetchFn, base: string, jobId: number) =>
		call<Score[]>(fetchFn, base, `/api/jobs/${jobId}/score-history`),

	setStatus: (
		fetchFn: FetchFn,
		base: string,
		id: number,
		status: ApplicationStatus,
		extra: StatusPayload = {}
	) =>
		call<Application>(fetchFn, base, `/api/jobs/${id}/status`, {
			method: 'PATCH',
			body: JSON.stringify({ status, ...extra })
		}),

	bulkSetStatus: (
		fetchFn: FetchFn,
		base: string,
		job_ids: number[],
		status: ApplicationStatus,
		extra: Omit<StatusPayload, 'notes'> = {}
	) =>
		call<Application[]>(fetchFn, base, `/api/jobs/bulk-status`, {
			method: 'POST',
			body: JSON.stringify({ job_ids, status, ...extra })
		}),

	getFollowups: (fetchFn: FetchFn, base: string) => call<Job[]>(fetchFn, base, `/api/followups`),

	setFollowup: (fetchFn: FetchFn, base: string, id: number, payload: FollowupPayload) =>
		call<Application>(fetchFn, base, `/api/jobs/${id}/followup`, {
			method: 'POST',
			body: JSON.stringify(payload)
		}),

	setNotes: (fetchFn: FetchFn, base: string, id: number, notes: string) =>
		call<Application>(fetchFn, base, `/api/jobs/${id}/notes`, {
			method: 'POST',
			body: JSON.stringify({ notes })
		}),

	setUnemployment: (fetchFn: FetchFn, base: string, id: number, used: boolean) =>
		call<Application>(fetchFn, base, `/api/jobs/${id}/unemployment`, {
			method: 'POST',
			body: JSON.stringify({ used })
		}),

	bulkSetUnemployment: (fetchFn: FetchFn, base: string, job_ids: number[], used: boolean) =>
		call<Application[]>(fetchFn, base, `/api/jobs/bulk-unemployment`, {
			method: 'POST',
			body: JSON.stringify({ job_ids, used })
		}),

	getCurrentResume: (fetchFn: FetchFn, base: string) =>
		callOptional<Resume>(fetchFn, base, '/api/resume/current'),

	getStaleScoreCount: (fetchFn: FetchFn, base: string) =>
		call<{ count: number }>(fetchFn, base, '/api/scores/stale-count'),

	uploadResume: async (fetchFn: FetchFn, base: string, file: File): Promise<Resume> => {
		const fd = new FormData();
		fd.append('file', file, file.name);
		const res = await fetchFn(`${base}/api/resume`, { method: 'POST', body: fd });
		if (!res.ok) throw new Error(`upload failed: ${res.status} ${await res.text()}`);
		return res.json() as Promise<Resume>;
	},

	resumePdfUrl: (base: string) => `${base}/api/resume/current/pdf`,

	getDraft: (fetchFn: FetchFn, base: string, jobId: number, includeMarkdown = false) =>
		callOptional<Draft>(
			fetchFn,
			base,
			`/api/jobs/${jobId}/draft${includeMarkdown ? '?include_markdown=true' : ''}`
		),

	renderDraft: (fetchFn: FetchFn, base: string, jobId: number) =>
		call<Draft>(fetchFn, base, `/api/jobs/${jobId}/draft/render`, { method: 'POST' }),

	draftResumePdfUrl: (base: string, jobId: number) => `${base}/api/jobs/${jobId}/draft/resume.pdf`,

	draftCoverLetterPdfUrl: (base: string, jobId: number) =>
		`${base}/api/jobs/${jobId}/draft/cover-letter.pdf`,

	getSearchProfile: (fetchFn: FetchFn, base: string) =>
		call<SearchProfile>(fetchFn, base, '/api/search-profile'),

	saveSearchProfile: (fetchFn: FetchFn, base: string, body: SearchProfileBody) =>
		call<SearchProfile>(fetchFn, base, '/api/search-profile', {
			method: 'PUT',
			body: JSON.stringify(body)
		}),

	clearRecommendations: (fetchFn: FetchFn, base: string) =>
		call<SearchProfile>(fetchFn, base, '/api/search-profile/recommendations', {
			method: 'DELETE'
		}),

	getProviders: (fetchFn: FetchFn, base: string) =>
		call<ProvidersResponse>(fetchFn, base, '/api/ai/providers'),

	getSelectedProvider: (fetchFn: FetchFn, base: string) =>
		call<{ selected: string | null }>(fetchFn, base, '/api/ai/selected'),

	selectProvider: (
		fetchFn: FetchFn,
		base: string,
		name: string,
		model?: string,
		scoringModel?: string
	) =>
		call<ProvidersResponse>(fetchFn, base, '/api/ai/provider', {
			method: 'PUT',
			body: JSON.stringify({ name, model, scoring_model: scoringModel })
		}),

	testProvider: (fetchFn: FetchFn, base: string, prompt?: string) =>
		call<AiTestResult>(fetchFn, base, '/api/ai/test', {
			method: 'POST',
			body: JSON.stringify({ prompt })
		}),

	startScorePending: (
		fetchFn: FetchFn,
		base: string,
		body: { job_ids?: number[]; include_stale?: boolean } = {}
	) =>
		call<{ task_id: string }>(fetchFn, base, '/api/ai/score-pending', {
			method: 'POST',
			body: JSON.stringify(body)
		}),

	getTask: (fetchFn: FetchFn, base: string, taskId: string) =>
		call<TaskSnapshot>(fetchFn, base, `/api/ai/tasks/${taskId}`),

	startIngest: (fetchFn: FetchFn, base: string) =>
		call<{ task_id: string }>(fetchFn, base, '/api/ingest', { method: 'POST' }),

	startDraft: (fetchFn: FetchFn, base: string, jobId: number) =>
		call<{ task_id: string }>(fetchFn, base, `/api/jobs/${jobId}/ai/draft`, {
			method: 'POST'
		}),

	startDraftBatch: (fetchFn: FetchFn, base: string, job_ids: number[]) =>
		call<{ task_id: string }>(fetchFn, base, '/api/ai/draft-batch', {
			method: 'POST',
			body: JSON.stringify({ job_ids })
		}),

	suggestRoles: (fetchFn: FetchFn, base: string) =>
		call<SearchProfile>(fetchFn, base, '/api/ai/suggest-roles', { method: 'POST' }),

	getUpdate: (fetchFn: FetchFn, base: string) => call<UpdateInfo>(fetchFn, base, '/api/update'),

	listBlacklist: (fetchFn: FetchFn, base: string) =>
		call<BlacklistedCompany[]>(fetchFn, base, '/api/blacklist'),

	addBlacklist: (fetchFn: FetchFn, base: string, name: string, reason?: string) =>
		call<BlacklistedCompany>(fetchFn, base, '/api/blacklist', {
			method: 'POST',
			body: JSON.stringify({ name, reason })
		}),

	removeBlacklist: async (fetchFn: FetchFn, base: string, id: number): Promise<void> => {
		const res = await fetchWithRetry(fetchFn, `${base}/api/blacklist/${id}`, { method: 'DELETE' });
		if (!res.ok && res.status !== 404) {
			throw new Error(`API /api/blacklist/${id} -> ${res.status}: ${await res.text()}`);
		}
	}
};
