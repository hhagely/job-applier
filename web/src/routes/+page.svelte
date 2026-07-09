<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		api,
		type ApplicationStatus,
		type Draft,
		type Job,
		type JobDetail,
		type TaskSnapshot
	} from '$lib/api';
	import { draftCart } from '$lib/draftCart.svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import ScoreBadge from '$lib/ScoreBadge.svelte';
	import Icon from '$lib/Icon.svelte';
	import { scoreBandVar } from '$lib/score';
	import { sourceInfo, type Ease } from '$lib/sources';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const isManual = $derived(data.filter_status === 'manual');

	// Provider gate for the detail-pane draft generation (scrape + score now live
	// on the Dashboard).
	const hasProvider = $derived(Boolean(data.aiProvider));

	type SortKey = 'score-desc' | 'score-asc' | 'posted-desc' | 'ingested-desc' | 'title-asc';
	type StatusFilter = ApplicationStatus | 'none';

	const EASE_FILTERS: { key: Ease; label: string }[] = [
		{ key: 'easy', label: 'easy' },
		{ key: 'med', label: 'medium' },
		{ key: 'hard', label: 'hard' }
	];

	const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
		{ key: 'none', label: 'unset' },
		{ key: 'new', label: 'new' },
		{ key: 'interested', label: 'interested' },
		{ key: 'drafted', label: 'drafted' },
		{ key: 'applied', label: 'applied' },
		{ key: 'screening', label: 'screening' },
		{ key: 'interviewing', label: 'interviewing' },
		{ key: 'rejected', label: 'rejected' },
		{ key: 'archived', label: 'archived' }
	];

	const BULK_STATUSES: ApplicationStatus[] = [
		'interested',
		'drafted',
		'applied',
		'screening',
		'interviewing',
		'rejected',
		'archived'
	];

	let sortBy = $state<SortKey>('score-desc');
	let activeStatuses = $state<Set<StatusFilter>>(new Set());
	let activeEases = $state<Set<Ease>>(new Set());
	let activeSources = $state<Set<string>>(new Set());
	let unscoredOnly = $state(false);
	let minScoreInput = $state('');

	type UnempFilter = 'used' | 'unused';
	const UNEMP_FILTERS: { key: UnempFilter; label: string }[] = [
		{ key: 'used', label: 'used' },
		{ key: 'unused', label: 'not used' }
	];
	let activeUnemp = $state<Set<UnempFilter>>(new Set());

	const FILTERS_STORAGE_KEY = 'job-applier:queue-filters';
	let filtersLoaded = $state(false);

	onMount(() => {
		try {
			const raw = localStorage.getItem(FILTERS_STORAGE_KEY);
			if (raw) {
				const s = JSON.parse(raw);
				if (typeof s.sortBy === 'string') sortBy = s.sortBy;
				if (Array.isArray(s.statuses)) activeStatuses = new Set(s.statuses);
				if (Array.isArray(s.eases)) activeEases = new Set(s.eases);
				if (Array.isArray(s.sources)) activeSources = new Set(s.sources);
				if (typeof s.unscoredOnly === 'boolean') unscoredOnly = s.unscoredOnly;
				if (Array.isArray(s.unemployment)) activeUnemp = new Set(s.unemployment);
				if (typeof s.minScoreInput === 'string') minScoreInput = s.minScoreInput;
			}
		} catch {
			// corrupt entry — fall through to defaults
		}
		filtersLoaded = true;
	});

	$effect(() => {
		if (!browser || !filtersLoaded) return;
		const payload = {
			sortBy,
			statuses: [...activeStatuses],
			eases: [...activeEases],
			sources: [...activeSources],
			unscoredOnly,
			unemployment: [...activeUnemp],
			minScoreInput
		};
		try {
			localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(payload));
		} catch {
			// quota / privacy mode — ignore
		}
	});

	let selected = $state<Set<number>>(new Set());
	let bulkStatus = $state<ApplicationStatus>('interested');
	let submitting = $state(false);
	let copied = $state(false);
	let copyTimer: ReturnType<typeof setTimeout> | null = null;
	let selectedId = $state<number | null>(null);

	// --- Batch draft (the Draft-list header button): kick off a background draft
	// of every job in the cart via the configured AI provider, then poll it. ---
	let draftBatchTask = $state<TaskSnapshot | null>(null);
	let draftBatchPolling = $state(false);
	let draftBatchStarting = $state(false);
	let draftBatchError = $state<string | null>(null);

	async function pollDraftBatchTask(taskId: string) {
		draftBatchPolling = true;
		const base = data.apiBase ?? '';
		try {
			// eslint-disable-next-line no-constant-condition
			while (true) {
				const snap = await api.getTask(fetch, base, taskId);
				draftBatchTask = snap;
				if (snap.status !== 'running') break;
				await new Promise((r) => setTimeout(r, 1000));
			}
			await invalidateAll();
		} catch (e) {
			draftBatchError = (e as Error).message;
		} finally {
			draftBatchPolling = false;
		}
	}

	function dismissDraftBatchPanel() {
		draftBatchTask = null;
		draftBatchError = null;
	}

	async function copySelectedIds() {
		const ids = [...selected].sort((a, b) => a - b).join(' ');
		try {
			await navigator.clipboard.writeText(ids);
			copied = true;
			if (copyTimer) clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = false), 1500);
		} catch {
			/* insecure context */
		}
	}

	function relTime(iso: string): string {
		const diff = Date.now() - new Date(iso).getTime();
		const days = Math.floor(diff / 86_400_000);
		if (days <= 0) return 'today';
		if (days === 1) return '1 day ago';
		if (days < 30) return `${days} days ago`;
		return `${Math.floor(days / 30)} months ago`;
	}

	function jobStatusKey(job: Job): StatusFilter {
		return job.application?.status ?? 'none';
	}

	function toggleIn<T>(set: Set<T>, key: T): Set<T> {
		const next = new Set(set);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		return next;
	}

	function isUsedForUnemployment(job: Job): boolean {
		return job.application?.used_for_unemployment ?? false;
	}

	function clearFilters() {
		activeStatuses = new Set();
		activeEases = new Set();
		activeSources = new Set();
		unscoredOnly = false;
		activeUnemp = new Set();
		minScoreInput = '';
	}

	const minScore = $derived.by(() => {
		const n = Number(minScoreInput);
		return minScoreInput !== '' && Number.isFinite(n) ? n : null;
	});

	function dateVal(iso: string | null | undefined): number {
		return iso ? new Date(iso).getTime() : 0;
	}

	const visible = $derived.by(() => {
		let list = data.jobs.slice();
		if (activeStatuses.size > 0) list = list.filter((j) => activeStatuses.has(jobStatusKey(j)));
		if (activeEases.size > 0) list = list.filter((j) => activeEases.has(sourceInfo(j.source).ease));
		if (activeSources.size > 0) list = list.filter((j) => activeSources.has(j.source));
		if (unscoredOnly) list = list.filter((j) => j.score == null);
		if (activeUnemp.size > 0) {
			list = list.filter((j) => activeUnemp.has(isUsedForUnemployment(j) ? 'used' : 'unused'));
		}
		if (minScore !== null) list = list.filter((j) => (j.score?.score ?? -1) >= minScore);
		const cmp: Record<SortKey, (a: Job, b: Job) => number> = {
			'score-desc': (a, b) => (b.score?.score ?? -1) - (a.score?.score ?? -1),
			'score-asc': (a, b) => (a.score?.score ?? 999) - (b.score?.score ?? 999),
			'posted-desc': (a, b) => dateVal(b.posted_at) - dateVal(a.posted_at),
			'ingested-desc': (a, b) => dateVal(b.ingested_at) - dateVal(a.ingested_at),
			'title-asc': (a, b) => a.title.localeCompare(b.title)
		};
		list.sort(cmp[sortBy]);
		return list;
	});

	// Selected job for the detail pane; falls back to the first visible row.
	const selectedJob = $derived(
		data.jobs.find((j) => j.id === selectedId) ?? visible[0] ?? null
	);

	function selectJob(id: number) {
		selectedId = id;
	}

	// --- Inline detail pane: load the full posting (description) + draft for the
	// selected row so triage happens without leaving the list. Score/status come
	// from the list payload (instant); description + draft are fetched per select. ---
	const base = $derived(data.apiBase ?? '');
	const STATUS_OPTS: ApplicationStatus[] = [
		'new',
		'interested',
		'drafted',
		'applied',
		'screening',
		'interviewing',
		'rejected',
		'archived'
	];

	let detailJob = $state<JobDetail | null>(null);
	let detailDraft = $state<Draft | null>(null);
	let detailLoading = $state(false);
	let detailErr = $state<string | null>(null);

	// Draft-generation (per selected job), mirrors the /jobs/[id] flow.
	let draftTask = $state<TaskSnapshot | null>(null);
	let draftPolling = $state(false);
	let draftStarting = $state(false);
	let draftError = $state<string | null>(null);

	// Status/notes/unemployment editing state, seeded when the selection changes.
	let pendingStatus = $state<ApplicationStatus>('new');
	let followupInput = $state<string>('');
	let seededFor = -1;
	const usedUnempInline = $derived(selectedJob?.application?.used_for_unemployment ?? false);

	$effect(() => {
		const j = selectedJob;
		if (j && j.id !== seededFor) {
			seededFor = j.id;
			pendingStatus = j.application?.status ?? 'new';
			const existing = j.application?.next_followup_at;
			followupInput = existing
				? new Date(existing).toISOString().slice(0, 10)
				: defaultFollowupDate();
		}
	});

	$effect(() => {
		const id = selectedJob?.id ?? null;
		// reset the draft panel when moving to a different job
		draftTask = null;
		draftError = null;
		if (id == null) {
			detailJob = null;
			detailDraft = null;
			detailErr = null;
			return;
		}
		let cancelled = false;
		detailLoading = true;
		detailErr = null;
		detailJob = null;
		detailDraft = null;
		(async () => {
			try {
				const [jd, dr] = await Promise.all([
					api.getJob(fetch, base, id),
					api.getDraft(fetch, base, id)
				]);
				if (!cancelled) {
					detailJob = jd;
					detailDraft = dr;
				}
			} catch (e) {
				if (!cancelled) detailErr = (e as Error).message;
			} finally {
				if (!cancelled) detailLoading = false;
			}
		})();
		return () => {
			cancelled = true;
		};
	});

	async function reloadDraft(id: number) {
		try {
			detailDraft = await api.getDraft(fetch, base, id);
		} catch {
			/* keep the stale draft rather than blanking the panel */
		}
	}

	async function pollDraftTask(taskId: string, jobId: number) {
		draftPolling = true;
		try {
			// eslint-disable-next-line no-constant-condition
			while (true) {
				const snap = await api.getTask(fetch, base, taskId);
				draftTask = snap;
				if (snap.status !== 'running') break;
				await new Promise((r) => setTimeout(r, 1000));
			}
			await invalidateAll(); // tailored score may have changed → refresh the row
			await reloadDraft(jobId);
		} catch (e) {
			draftError = (e as Error).message;
		} finally {
			draftPolling = false;
		}
	}

	function dismissDraftPanel() {
		draftTask = null;
		draftError = null;
	}

	// Generic enhance for the inline status/notes/unemployment forms: refresh the
	// list on success (so the row's status tag updates) without resetting inputs.
	function detailEnhance() {
		return async ({
			result,
			update
		}: {
			result: { type: string };
			update: (opts?: { reset?: boolean }) => Promise<void>;
		}) => {
			if (result.type === 'success') await invalidateAll();
			await update({ reset: false });
		};
	}

	const allVisibleSelected = $derived(
		visible.length > 0 && visible.every((j) => selected.has(j.id))
	);
	const someVisibleSelected = $derived(visible.some((j) => selected.has(j.id)));

	function toggleSelect(id: number) {
		selected = toggleIn(selected, id);
	}

	function toggleSelectAllVisible() {
		const next = new Set(selected);
		if (allVisibleSelected) for (const j of visible) next.delete(j.id);
		else for (const j of visible) next.add(j.id);
		selected = next;
	}

	function clearSelection() {
		selected = new Set();
	}

	const selectedCount = $derived(selected.size);

	function statusCount(key: StatusFilter): number {
		return data.jobs.filter((j) => jobStatusKey(j) === key).length;
	}
	function easeCount(key: Ease): number {
		return data.jobs.filter((j) => sourceInfo(j.source).ease === key).length;
	}
	function sourceCount(key: string): number {
		return data.jobs.filter((j) => j.source === key).length;
	}
	function unempCount(key: UnempFilter): number {
		return data.jobs.filter((j) => isUsedForUnemployment(j) === (key === 'used')).length;
	}

	// Only offer source facets that actually appear in the current queue.
	const SOURCE_FILTERS = $derived([...new Set(data.jobs.map((j) => j.source))].sort());

	function rubricEntries(rubric: Record<string, unknown> | undefined): [string, unknown][] {
		if (!rubric) return [];
		return Object.entries(rubric);
	}

	function rubricNumber(value: unknown): number | null {
		return typeof value === 'number' && value >= 0 && value <= 100 ? value : null;
	}

	function isFollowupDue(job: Job): boolean {
		const due = job.application?.next_followup_at;
		if (!due || job.application?.outcome) return false;
		return new Date(due).getTime() <= Date.now();
	}

	function defaultFollowupDate(): string {
		return new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10);
	}
	let followupDate = $state<string>(defaultFollowupDate());

	function toggleDuplicates() {
		const url = new URL($page.url);
		if (data.include_duplicates) url.searchParams.delete('duplicates');
		else url.searchParams.set('duplicates', '1');
		goto(url, { invalidateAll: true });
	}

	function switchQueue(manual: boolean) {
		const url = new URL($page.url);
		if (manual) url.searchParams.set('filter', 'manual');
		else url.searchParams.delete('filter');
		selectedId = null;
		goto(url, { invalidateAll: true });
	}

	const hasActiveFilters = $derived(
		activeStatuses.size > 0 ||
			activeEases.size > 0 ||
			activeSources.size > 0 ||
			activeUnemp.size > 0 ||
			unscoredOnly ||
			minScoreInput !== ''
	);

	// J / K move the detail selection through the visible list.
	function onQueueKey(e: KeyboardEvent) {
		const target = e.target as HTMLElement | null;
		if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
		if (e.metaKey || e.ctrlKey || e.altKey) return;
		const key = e.key.toLowerCase();
		if (key !== 'j' && key !== 'k') return;
		if (visible.length === 0) return;
		e.preventDefault();
		const cur = selectedJob ? visible.findIndex((j) => j.id === selectedJob.id) : -1;
		const ni = key === 'j' ? Math.min(cur + 1, visible.length - 1) : Math.max(cur - 1, 0);
		const next = visible[ni];
		if (next) {
			selectedId = next.id;
			document.getElementById(`jrow-${next.id}`)?.scrollIntoView({ block: 'nearest' });
		}
	}
</script>

<svelte:window onkeydown={onQueueKey} />

<div class="view-head">
	<div class="vh-titles">
		<h1>{isManual ? 'Manual review' : 'Queue'}</h1>
		<div class="vh-sub">
			{visible.length}{visible.length === data.jobs.length ? '' : ` of ${data.jobs.length}`} jobs · sorted by match score
		</div>
	</div>
	<div class="vh-actions">
		<button class="btn" onclick={clearFilters} disabled={!hasActiveFilters}>Clear filters</button>
		{#if !hasProvider}
			<a
				class="btn primary"
				href="/settings"
				title="Select an AI CLI in Settings to draft in-app"
			>
				<Icon name="doc" size={15} stroke={2} /> Draft list — set up AI
			</a>
		{:else}
			<form
				method="POST"
				action="?/draftBatch"
				use:enhance={() => {
					draftBatchStarting = true;
					draftBatchError = null;
					return async ({ result }) => {
						draftBatchStarting = false;
						if (result.type === 'success' && result.data?.task_id) {
							draftBatchTask = null;
							pollDraftBatchTask(result.data.task_id as string);
						} else if (result.type === 'failure') {
							draftBatchError = (result.data?.error as string) ?? 'could not start drafting';
						}
					};
				}}
			>
				{#each draftCart.ids as id (id)}
					<input type="hidden" name="ids" value={id} />
				{/each}
				<button
					type="submit"
					class="btn primary"
					disabled={draftCart.ids.length === 0 || draftBatchStarting || draftBatchPolling}
					title={draftCart.ids.length === 0
						? 'Add jobs to your draft list first'
						: `Draft ${draftCart.ids.length} job${draftCart.ids.length === 1 ? '' : 's'} via ${data.aiProvider}`}
				>
					<Icon name="doc" size={15} stroke={2} />
					{#if draftBatchStarting || draftBatchPolling}
						Drafting…
					{:else if draftCart.ids.length === 0}
						Draft list empty
					{:else}
						Draft {draftCart.ids.length} item{draftCart.ids.length === 1 ? '' : 's'}
					{/if}
				</button>
			</form>
		{/if}
	</div>
</div>

{#if draftBatchError && !draftBatchTask}
	<div class="q-panels"><p class="err-text">{draftBatchError}</p></div>
{/if}
{#if draftBatchTask}
	<div class="q-panels">
		<ScoreProgress
			task={draftBatchTask}
			onDismiss={dismissDraftBatchPanel}
			runningVerb="Drafting"
			doneVerb="Drafted"
			resultsLabel="drafts"
		/>
	</div>
{/if}

<div class="md">
	<!-- master list -->
	<div class="md-list">
		<div class="toolbar">
			<div class="qtabs">
				<button class="qtab" aria-pressed={!isManual} onclick={() => switchQueue(false)}>
					Review queue
				</button>
				<button class="qtab" aria-pressed={isManual} onclick={() => switchQueue(true)}>
					Manual review
				</button>
			</div>

			<div class="toolbar-row">
				<select class="mini-input" bind:value={sortBy} style="width:auto" aria-label="Sort">
					<option value="score-desc">Score (high → low)</option>
					<option value="score-asc">Score (low → high)</option>
					<option value="posted-desc">Posted (newest)</option>
					<option value="ingested-desc">Ingested (newest)</option>
					<option value="title-asc">Title (A → Z)</option>
				</select>
				<input class="mini-input" type="number" min="0" max="100" placeholder="min score" style="width:88px" bind:value={minScoreInput} aria-label="Minimum score" />
				<button class="chip" aria-pressed={unscoredOnly} onclick={() => (unscoredOnly = !unscoredOnly)}>Unscored only</button>
				<button class="chip" aria-pressed={data.include_duplicates} onclick={toggleDuplicates}>Show duplicates</button>
			</div>

			{#if !isManual}
				<div class="toolbar-row">
					<span class="lbl">Ease</span>
					{#each EASE_FILTERS as f (f.key)}
						{@const n = easeCount(f.key)}
						{#if n > 0}
							<button class="chip" aria-pressed={activeEases.has(f.key)} onclick={() => (activeEases = toggleIn(activeEases, f.key))}>
								{f.label} <span class="c-n">{n}</span>
							</button>
						{/if}
					{/each}
				</div>
				<div class="toolbar-row">
					<span class="lbl">Source</span>
					{#each SOURCE_FILTERS as key (key)}
						{@const n = sourceCount(key)}
						{#if n > 0}
							<button class="chip" aria-pressed={activeSources.has(key)} onclick={() => (activeSources = toggleIn(activeSources, key))}>
								{sourceInfo(key).label} <span class="c-n">{n}</span>
							</button>
						{/if}
					{/each}
				</div>
				<div class="toolbar-row">
					<span class="lbl">Status</span>
					{#each STATUS_FILTERS as f (f.key)}
						{@const n = statusCount(f.key)}
						{#if n > 0}
							<button class="chip" aria-pressed={activeStatuses.has(f.key)} onclick={() => (activeStatuses = toggleIn(activeStatuses, f.key))}>
								{f.label} <span class="c-n">{n}</span>
							</button>
						{/if}
					{/each}
				</div>
				<div class="toolbar-row">
					<span class="lbl">Unemp.</span>
					{#each UNEMP_FILTERS as f (f.key)}
						<button class="chip" aria-pressed={activeUnemp.has(f.key)} onclick={() => (activeUnemp = toggleIn(activeUnemp, f.key))}>
							{f.label} <span class="c-n">{unempCount(f.key)}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>

		<div class="list-scroll">
			{#if data.jobs.length === 0}
				<p class="list-empty">Nothing here. Run a scrape from the Dashboard to pull new postings.</p>
			{:else if visible.length === 0}
				<p class="list-empty">No jobs match the current filters.</p>
			{:else}
				{#each visible as job (job.id)}
					{@const si = sourceInfo(job.source)}
					<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
					<div
						id="jrow-{job.id}"
						class="jrow"
						class:sel={selectedJob?.id === job.id}
						class:dup={job.duplicate_of != null}
						onclick={() => selectJob(job.id)}
					>
						<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
						<input
							type="checkbox"
							checked={selected.has(job.id)}
							onclick={(e) => e.stopPropagation()}
							onchange={() => toggleSelect(job.id)}
							aria-label="select job"
						/>
						<ScoreBadge score={job.score?.score ?? null} size="sm" stale={job.score?.is_stale ?? false} />
						<div class="jr-main">
							<div class="jr-title">{job.title}</div>
							<div class="jr-meta">
								<span class="pill src-{job.source}"><span class="dot-badge"></span>{si.label}</span>
								<span class="co">{job.company?.name ?? 'Unknown'}</span>
								{#if job.location}· <span>{job.location}</span>{/if}
								{#if job.application}<span class="tag status-{job.application.status}">{job.application.status}</span>{/if}
								{#if isFollowupDue(job)}<span class="tag" style="color:var(--good)">follow-up due</span>{/if}
								{#if isUsedForUnemployment(job)}<span class="tag status-applied">✓ unemployment</span>{/if}
								{#if job.duplicate_of != null}<span class="tag" style="color:var(--good)">dup #{job.duplicate_of}</span>{/if}
								{#if draftCart.has(job.id)}<span class="tag status-draft">in draft</span>{/if}
							</div>
						</div>
					</div>
				{/each}
			{/if}
		</div>

		{#if selectedCount > 0}
			<div class="bulkbar">
				<b>{selectedCount}</b> selected
				<button type="button" class="btn sm" style="margin-left:auto" onclick={copySelectedIds}>
					{copied ? 'Copied!' : 'Copy IDs'}
				</button>
				<button type="button" class="btn sm" onclick={clearSelection}>Clear</button>
			</div>
		{/if}

		<div class="list-foot">
			<label>
				<input
					type="checkbox"
					checked={allVisibleSelected}
					indeterminate={!allVisibleSelected && someVisibleSelected}
					onchange={toggleSelectAllVisible}
				/>
				Select all visible ({visible.length})
			</label>
			<span class="mono" style="margin-left:auto">{visible.length} shown</span>
		</div>
	</div>

	<!-- detail pane -->
	<div class="detail">
		{#if !selectedJob}
			<div class="empty-detail">Select a job to see its full posting and take action.</div>
		{:else}
			{@const j = selectedJob}
			{@const si = sourceInfo(j.source)}
			<div class="detail-inner">
				<div class="d-top">
					{#if draftCart.has(j.id)}<span class="tag status-draft">✓ in draft list</span>{/if}
					{#if j.application}<span class="tag status-{j.application.status}">{j.application.status}</span>{/if}
					<span class="mono" style="color:var(--faint);font-size:12px">{si.ease} apply</span>
				</div>
				<div class="d-title">{j.title}</div>
				<div class="d-org">
					<span class="pill src-{j.source}"><span class="dot-badge"></span>{si.label}</span>
					<b style="color:var(--fg)">{j.company?.name ?? 'Unknown'}</b>
					{#if j.location}· {j.location}{/if}
					{#if j.posted_at}· posted {relTime(j.posted_at)}{/if}
					· ingested {relTime(j.ingested_at)}
					{#if j.employment_type}· {j.employment_type}{/if}
				</div>
				<div class="d-actions">
					<button type="button" class="btn sm" class:primary={draftCart.has(j.id)} onclick={() => draftCart.toggle(j.id)}>
						{draftCart.has(j.id) ? '✓ In draft list' : '+ Add to draft list'}
					</button>
					<a class="d-link" href={j.url} target="_blank" rel="noopener">
						View original posting <Icon name="external" size={12} stroke={2} />
					</a>
				</div>

				<div class="d-cols">
					<div class="card">
						<div class="card-h">
							<h3>Match score</h3>
							{#if j.score}<span class="tag" style="margin-left:auto">{j.score.score_kind}</span>{/if}
						</div>
						<div class="card-b">
							{#if !j.score}
								<div class="draft-empty">Not scored yet. Run <code>/match-pending</code> or the Score-pending button.</div>
							{:else}
								{#if j.score.is_stale}
									<p class="banner warn" style="margin-bottom:12px">Scored against an older resume — re-score to refresh.</p>
								{/if}
								<div class="match-hero">
									<span class="mh-n" style="color:{scoreBandVar(j.score.score)}">{j.score.score}</span>
									<span class="mh-d">/100</span>
								</div>
								{#if j.score.reasoning}<div class="rationale">{j.score.reasoning}</div>{/if}
								{#if rubricEntries(j.score.rubric).length > 0}
									<details class="rubric">
										<summary><Icon name="chevron" size={12} stroke={2.4} /> Rubric breakdown</summary>
										{#each rubricEntries(j.score.rubric) as [label, value] (label)}
											{@const num = rubricNumber(value)}
											<div class="rub-row">
												<div class="rr-l">{label}</div>
												{#if num !== null}
													<div class="rr-track"><div class="rr-fill" style="width:{num}%;background:{scoreBandVar(num)}"></div></div>
													<div class="rr-n">{num}</div>
												{:else}
													<div class="rr-v">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</div>
												{/if}
											</div>
										{/each}
									</details>
								{/if}
							{/if}
						</div>
					</div>

					<div class="card">
						<div class="card-h"><h3>Status &amp; tracking</h3></div>
						<div class="card-b">
							<form method="POST" action="?/setStatus" use:enhance={detailEnhance}>
								<input type="hidden" name="job_id" value={j.id} />
								<div class="row-form">
									<select class="input" name="status" bind:value={pendingStatus} style="flex:1;min-width:9rem">
										{#each STATUS_OPTS as s (s)}<option value={s}>{s}</option>{/each}
									</select>
									{#if pendingStatus === 'applied'}
										<input class="input" type="date" name="next_followup_at" bind:value={followupInput} style="width:auto" />
									{/if}
									<button type="submit" class="btn">Update</button>
								</div>
							</form>
							{#if j.application?.next_followup_at}
								<p class="muted small">
									Next follow-up: {new Date(j.application.next_followup_at).toLocaleDateString()}
									{#if j.application.outcome}· outcome: <strong>{j.application.outcome}</strong>{/if}
								</p>
							{/if}

							<div class="field-label">Unemployment claim</div>
							<form method="POST" action="?/setUnemployment" class="row-form" use:enhance={detailEnhance}>
								<input type="hidden" name="job_id" value={j.id} />
								<input type="hidden" name="used" value={usedUnempInline ? 'false' : 'true'} />
								<button type="submit" class="btn" class:primary={usedUnempInline}>
									{usedUnempInline ? '✓ Used for unemployment' : 'Mark used for unemployment'}
								</button>
							</form>

							<div class="field-label">Notes</div>
							{#key j.id}
								<form method="POST" action="?/setNotes" use:enhance={detailEnhance}>
									<input type="hidden" name="job_id" value={j.id} />
									<textarea class="input" name="notes" rows="3" placeholder="Personal notes about this role…">{j.application?.notes ?? ''}</textarea>
									<button type="submit" class="btn sm" style="margin-top:8px">Save notes</button>
								</form>
							{/key}
						</div>
					</div>
				</div>

				<div class="card" style="margin-top:14px">
					<div class="card-h"><h3>Tailored draft</h3></div>
					<div class="card-b">
						<div style="margin-bottom:12px">
							{#if !hasProvider}
								<a class="btn" href="/settings" title="Select an AI CLI in Settings">Generate tailored draft — set up AI</a>
							{:else}
								<form
									method="POST"
									action="?/generateDraft"
									use:enhance={() => {
										draftStarting = true;
										draftError = null;
										const jobId = j.id;
										return async ({ result }) => {
											draftStarting = false;
											if (result.type === 'success' && result.data?.task_id) {
												draftTask = null;
												pollDraftTask(result.data.task_id as string, jobId);
											} else if (result.type === 'failure') {
												draftError = (result.data?.error as string) ?? 'could not start drafting';
											}
										};
									}}
								>
									<input type="hidden" name="job_id" value={j.id} />
									<button type="submit" class="btn primary" disabled={draftStarting || draftPolling}>
										{#if draftStarting || draftPolling}
											Generating…
										{:else if detailDraft && (detailDraft.has_resume_md || detailDraft.has_cover_letter_md)}
											Regenerate tailored draft
										{:else}
											Generate tailored draft
										{/if}
									</button>
								</form>
							{/if}
						</div>
						{#if draftError && !draftTask}<p class="err-text" style="margin-bottom:12px">{draftError}</p>{/if}
						{#if draftTask}
							<ScoreProgress task={draftTask} onDismiss={dismissDraftPanel} runningVerb="Generating" doneVerb="Generated" resultsLabel="stages" />
						{/if}
						{#if detailDraft && (detailDraft.has_resume_pdf || detailDraft.has_cover_letter_pdf)}
							<div class="draft-actions">
								{#if detailDraft.has_resume_pdf}<a class="btn primary" href={api.draftResumePdfUrl(base, j.id)} download>Download resume PDF</a>{/if}
								{#if detailDraft.has_cover_letter_pdf}<a class="btn primary" href={api.draftCoverLetterPdfUrl(base, j.id)} download>Download cover letter PDF</a>{/if}
							</div>
							<form method="POST" action="?/renderDraft" use:enhance={detailEnhance} style="margin-top:8px">
								<input type="hidden" name="job_id" value={j.id} />
								<button type="submit" class="btn sm">Re-render PDFs from markdown</button>
							</form>
						{:else if hasProvider}
							<p class="muted small">No draft yet. Generate above, or run <code>/draft {j.id}</code> in Claude Code.</p>
						{/if}
					</div>
				</div>

				<div class="card" style="margin-top:14px">
					<div class="card-h"><h3>Description</h3></div>
					<div class="card-b">
						{#if detailErr}
							<p class="err-text">Couldn't load the description: {detailErr}</p>
						{:else if detailJob}
							<div class="description">
								<!-- eslint-disable-next-line svelte/no-at-html-tags -->
								{@html detailJob.description}
							</div>
						{:else}
							<p class="muted small">Loading description…</p>
						{/if}
						{#if j.filter_reason}<p class="banner warn" style="margin-top:12px">{j.filter_reason}</p>{/if}
					</div>
				</div>
			</div>
		{/if}
	</div>
</div>

{#if selectedCount > 0}
	<form
		method="POST"
		action="?/bulkStatus"
		class="floater action-bar"
		use:enhance={() => {
			submitting = true;
			return async ({ result, update }) => {
				submitting = false;
				if (result.type === 'success') {
					selected = new Set();
					await invalidateAll();
				}
				await update({ reset: false });
			};
		}}
	>
		<span class="action-count">{selectedCount} selected</span>
		{#each [...selected] as id (id)}<input type="hidden" name="ids" value={id} />{/each}
		<label class="action-status">
			Status
			<select class="mini-input" name="status" bind:value={bulkStatus}>
				{#each BULK_STATUSES as s (s)}<option value={s}>{s}</option>{/each}
			</select>
		</label>
		{#if bulkStatus === 'applied'}
			<label class="action-status">
				Follow up
				<input class="mini-input" type="date" name="next_followup_at" bind:value={followupDate} />
			</label>
		{/if}
		<button type="submit" class="btn sm primary" disabled={submitting}>{submitting ? 'Applying…' : 'Apply'}</button>
		<span class="action-sep" aria-hidden="true"></span>
		<button type="submit" formaction="?/bulkUnemployment" name="used" value="true" class="btn sm" disabled={submitting} title="Mark selected as used for an unemployment claim">✓ Unemployment</button>
		<button type="submit" formaction="?/bulkUnemployment" name="used" value="false" class="btn sm ghost" disabled={submitting} title="Clear the unemployment flag on selected">Unmark</button>
		<button type="button" class="btn sm" onclick={copySelectedIds}>{copied ? 'Copied!' : 'Copy IDs'}</button>
		<button type="button" class="btn sm ghost" onclick={clearSelection}>Clear</button>
	</form>
{/if}

<style>
	.md {
		flex: 1;
		min-height: 0;
		display: flex;
	}
	.md-list {
		width: min(46%, 560px);
		flex: none;
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.q-panels {
		flex: none;
		padding: 12px 16px 0;
	}
	.toolbar {
		flex: none;
		padding: 11px 14px;
		border-bottom: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.toolbar-row {
		display: flex;
		align-items: center;
		gap: 9px;
		flex-wrap: wrap;
	}
	.toolbar-row .lbl {
		font-size: 11px;
		font-weight: 640;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--faint);
		width: 52px;
		flex: none;
	}
	.qtabs {
		display: flex;
		gap: 4px;
		padding: 3px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 9px;
	}
	.qtab {
		flex: 1;
		height: 29px;
		border-radius: 7px;
		font-size: 12px;
		font-weight: 560;
		color: var(--muted);
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
	}
	.qtab:hover {
		color: var(--fg);
	}
	.qtab[aria-pressed='true'] {
		background: var(--surface);
		color: var(--fg);
		box-shadow: 0 1px 3px oklch(0% 0 0 / 0.16);
	}
	.list-scroll {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
	}
	.list-empty {
		padding: 48px 20px;
		text-align: center;
		color: var(--faint);
		font-size: 13px;
	}
	.jrow {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 14px;
		border-bottom: 1px solid var(--border);
		cursor: pointer;
		position: relative;
	}
	.jrow:hover {
		background: var(--surface-2);
	}
	.jrow.sel {
		background: var(--accent-soft);
	}
	.jrow.sel::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 3px;
		background: var(--accent);
	}
	.jrow.dup {
		opacity: 0.6;
	}
	.jrow.dup:hover {
		opacity: 1;
	}
	.jrow input[type='checkbox'] {
		width: 15px;
		height: 15px;
		accent-color: var(--accent);
		flex: none;
	}
	.jr-main {
		min-width: 0;
		flex: 1;
	}
	.jr-title {
		font-weight: 600;
		font-size: 13px;
		line-height: 1.35;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.jr-meta {
		display: flex;
		align-items: center;
		gap: 7px;
		margin-top: 5px;
		color: var(--faint);
		font-size: 11.5px;
		flex-wrap: wrap;
	}
	.jr-meta .co {
		color: var(--muted);
		font-weight: 560;
	}
	.list-foot {
		flex: none;
		border-top: 1px solid var(--border);
		padding: 9px 14px;
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 12px;
		color: var(--muted);
		background: var(--surface);
	}
	.list-foot label {
		display: flex;
		align-items: center;
		gap: 8px;
		cursor: pointer;
	}
	.list-foot input {
		width: 15px;
		height: 15px;
		accent-color: var(--accent);
	}
	.bulkbar {
		flex: none;
		padding: 10px 14px;
		border-top: 1px solid var(--border);
		background: var(--accent-soft);
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 12.5px;
	}

	.detail {
		flex: 1;
		min-width: 0;
		overflow-y: auto;
	}
	.detail-inner {
		padding: 22px 26px 44px;
		max-width: 760px;
	}
	.d-top {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		margin-bottom: 14px;
	}
	.d-title {
		font-size: 22px;
		letter-spacing: -0.02em;
		font-weight: 660;
		line-height: 1.2;
	}
	.d-org {
		display: flex;
		align-items: center;
		gap: 9px;
		color: var(--muted);
		font-size: 13px;
		margin-top: 8px;
		flex-wrap: wrap;
	}
	.d-cols {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
		margin-top: 18px;
	}
	@media (max-width: 900px) {
		.d-cols {
			grid-template-columns: 1fr;
		}
	}
	.match-hero {
		display: flex;
		align-items: baseline;
		gap: 6px;
	}
	.match-hero .mh-n {
		font-family: var(--mono);
		font-size: 44px;
		font-weight: 680;
		letter-spacing: -0.03em;
		line-height: 1;
	}
	.match-hero .mh-d {
		font-family: var(--mono);
		color: var(--faint);
		font-size: 16px;
	}
	.rationale {
		color: var(--muted);
		font-size: 13px;
		line-height: 1.6;
		margin-top: 12px;
	}
	details.rubric {
		margin-top: 14px;
		border-top: 1px solid var(--border);
		padding-top: 12px;
	}
	details.rubric summary {
		cursor: pointer;
		font-size: 12px;
		font-weight: 600;
		color: var(--accent);
		list-style: none;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	details.rubric summary::-webkit-details-marker {
		display: none;
	}
	details.rubric summary :global(svg) {
		transition: transform 0.15s;
	}
	details.rubric[open] summary :global(svg) {
		transform: rotate(90deg);
	}
	.rub-row {
		display: grid;
		grid-template-columns: 150px 1fr 34px;
		gap: 10px;
		align-items: center;
		margin-top: 11px;
	}
	.rub-row .rr-l {
		font-size: 12px;
		color: var(--muted);
	}
	.rub-row .rr-track {
		height: 7px;
		border-radius: 20px;
		background: var(--surface-2);
		overflow: hidden;
	}
	.rub-row .rr-fill {
		height: 100%;
		border-radius: 20px;
	}
	.rub-row .rr-n {
		font-family: var(--mono);
		font-size: 12px;
		text-align: right;
		color: var(--muted);
	}
	.rub-row .rr-v {
		grid-column: 2 / 4;
		font-family: var(--mono);
		font-size: 11.5px;
		color: var(--muted);
	}
	.draft-empty {
		color: var(--muted);
		font-size: 12.5px;
		line-height: 1.6;
	}
	.empty-detail {
		height: 100%;
		display: grid;
		place-items: center;
		color: var(--faint);
		text-align: center;
		padding: 40px;
	}
	.d-actions {
		margin-top: 12px;
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
	}
	.row-form {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
	}
	.field-label {
		font-size: 12px;
		font-weight: 600;
		color: var(--fg);
		margin: 16px 0 8px;
	}
	.small {
		font-size: 11.5px;
		margin-top: 8px;
	}
	.draft-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	/* imported job description HTML — normalize into the theme */
	.description :global(*) {
		color: var(--fg) !important;
		background-color: transparent !important;
		max-width: 100%;
	}
	.description :global(a) {
		color: var(--accent) !important;
	}
	.description :global(p) {
		line-height: 1.65;
		margin: 0 0 0.75em;
		font-size: 13px;
	}
	.description :global(ul),
	.description :global(ol) {
		padding-left: 1.4rem;
		margin: 0 0 0.75em;
		font-size: 13px;
	}
	.description :global(li) {
		margin: 0.2em 0;
	}
	.description :global(h1),
	.description :global(h2),
	.description :global(h3) {
		font-size: 14px;
		margin: 1em 0 0.4em;
	}
	.description :global(img) {
		max-width: 100%;
		height: auto;
	}
	.description :global(pre),
	.description :global(code) {
		background: var(--surface-2) !important;
		font-family: var(--mono);
	}
	.description :global(pre) {
		padding: 0.6rem;
		border-radius: 8px;
		overflow-x: auto;
	}

	/* floating bottom bars (above the 27px status bar) */
	.floater {
		position: fixed;
		left: 50%;
		transform: translateX(-50%);
		bottom: 2.5rem;
		display: flex;
		align-items: center;
		gap: 0.7rem;
		padding: 0.55rem 0.85rem;
		background: var(--surface);
		border: 1px solid var(--border-2);
		border-radius: 12px;
		box-shadow: var(--shadow);
		font-size: 12.5px;
		z-index: 40;
		max-width: 92vw;
	}
	.action-bar {
		border-color: var(--accent);
		flex-wrap: wrap;
	}
	.action-count {
		color: var(--accent);
		font-weight: 600;
	}
	.action-status {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--muted);
	}
	.action-sep {
		width: 1px;
		align-self: stretch;
		background: var(--border);
	}
</style>
