<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/stores';
	import { api, type ApplicationStatus, type Job, type TaskSnapshot } from '$lib/api';
	import { draftCart } from '$lib/draftCart.svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import ScoreBadge from '$lib/ScoreBadge.svelte';
	import Icon from '$lib/Icon.svelte';
	import { scoreBandVar } from '$lib/score';
	import { sourceInfo, type Ease } from '$lib/sources';
	import { onCommand } from '$lib/shell/commandBus';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const isManual = $derived(data.filter_status === 'manual');

	// --- In-app scoring (Phase 4) ---
	const pendingCount = $derived(
		data.jobs.filter((j) => j.score == null || j.score?.is_stale).length
	);
	const hasProvider = $derived(Boolean(data.aiProvider));
	const showScoreButton = $derived(data.filter_status === 'passed');

	let scoreTask = $state<TaskSnapshot | null>(null);
	let scorePolling = $state(false);
	let scoreStarting = $state(false);
	let scoreError = $state<string | null>(null);
	let scoreForm = $state<HTMLFormElement | null>(null);

	async function pollScoreTask(taskId: string) {
		scorePolling = true;
		const base = data.apiBase ?? '';
		try {
			// eslint-disable-next-line no-constant-condition
			while (true) {
				const snap = await api.getTask(fetch, base, taskId);
				scoreTask = snap;
				if (snap.status !== 'running') break;
				await new Promise((r) => setTimeout(r, 1000));
			}
			await invalidateAll();
		} catch (e) {
			scoreError = (e as Error).message;
		} finally {
			scorePolling = false;
		}
	}

	function dismissScorePanel() {
		scoreTask = null;
		scoreError = null;
	}

	// --- Ingest / scrape (Phase 6.5) ---
	let ingestTask = $state<TaskSnapshot | null>(null);
	let ingestPolling = $state(false);
	let ingestStarting = $state(false);
	let ingestError = $state<string | null>(null);
	let scrapeForm = $state<HTMLFormElement | null>(null);

	async function pollIngestTask(taskId: string) {
		ingestPolling = true;
		const base = data.apiBase ?? '';
		try {
			// eslint-disable-next-line no-constant-condition
			while (true) {
				const snap = await api.getTask(fetch, base, taskId);
				ingestTask = snap;
				if (snap.status !== 'running') break;
				await new Promise((r) => setTimeout(r, 1000));
			}
			await invalidateAll();
		} catch (e) {
			ingestError = (e as Error).message;
		} finally {
			ingestPolling = false;
		}
	}

	function dismissIngestPanel() {
		ingestTask = null;
		ingestError = null;
	}

	// Command palette can trigger scrape/score by submitting the real forms.
	onMount(() =>
		onCommand((name) => {
			if (name === 'scrape') scrapeForm?.requestSubmit();
			else if (name === 'score' && hasProvider && pendingCount > 0) scoreForm?.requestSubmit();
		})
	);

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
	let draftCopied = $state(false);
	let draftCopyTimer: ReturnType<typeof setTimeout> | null = null;

	let selectedId = $state<number | null>(null);

	async function copyDraftCommand() {
		try {
			await navigator.clipboard.writeText(draftCart.command);
			draftCopied = true;
			if (draftCopyTimer) clearTimeout(draftCopyTimer);
			draftCopyTimer = setTimeout(() => (draftCopied = false), 1500);
		} catch {
			/* insecure context */
		}
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
		<form
			bind:this={scrapeForm}
			method="POST"
			action="?/runIngest"
			use:enhance={() => {
				ingestStarting = true;
				ingestError = null;
				return async ({ result }) => {
					ingestStarting = false;
					if (result.type === 'success' && result.data?.task_id) {
						ingestTask = null;
						pollIngestTask(result.data.task_id as string);
					} else if (result.type === 'failure') {
						ingestError = (result.data?.error as string) ?? 'could not start scrape';
					}
				};
			}}
		>
			<button
				type="submit"
				class="btn"
				disabled={ingestStarting || ingestPolling}
				title="Pull new postings from every source"
			>
				<Icon name="refresh" size={15} stroke={2} />
				{ingestStarting || ingestPolling ? 'Scraping…' : 'Run scrape'}
			</button>
		</form>

		{#if showScoreButton}
			{#if !hasProvider}
				<a class="btn danger" href="/settings" title="Select an AI CLI in Settings">
					Score pending — set up AI
				</a>
			{:else}
				<form
					bind:this={scoreForm}
					method="POST"
					action="?/scorePending"
					use:enhance={() => {
						scoreStarting = true;
						scoreError = null;
						return async ({ result }) => {
							scoreStarting = false;
							if (result.type === 'success' && result.data?.task_id) {
								scoreTask = null;
								pollScoreTask(result.data.task_id as string);
							} else if (result.type === 'failure') {
								scoreError = (result.data?.error as string) ?? 'could not start scoring';
							}
						};
					}}
				>
					<button
						type="submit"
						class="btn primary"
						disabled={pendingCount === 0 || scoreStarting || scorePolling}
						title={pendingCount === 0
							? 'Nothing to score'
							: `Score ${pendingCount} pending via ${data.aiProvider}`}
					>
						<Icon name="star" size={15} stroke={2} />
						{scoreStarting || scorePolling ? 'Scoring…' : `Score pending (${pendingCount})`}
					</button>
				</form>
			{/if}
		{/if}
	</div>
</div>

{#if ingestError && !ingestTask}
	<div class="q-panels"><p class="err-text">{ingestError}</p></div>
{/if}
{#if ingestTask}
	<div class="q-panels">
		<ScoreProgress task={ingestTask} onDismiss={dismissIngestPanel} runningVerb="Scraping" doneVerb="Scraped" resultsLabel="sources" />
	</div>
{/if}
{#if scoreError && !scoreTask}
	<div class="q-panels"><p class="err-text">{scoreError}</p></div>
{/if}
{#if scoreTask}
	<div class="q-panels"><ScoreProgress task={scoreTask} onDismiss={dismissScorePanel} /></div>
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
				{#if hasActiveFilters}
					<button class="chip" onclick={clearFilters}>Clear filters</button>
				{/if}
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
				<p class="list-empty">Nothing here. Run a scrape to pull new postings.</p>
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
			<div class="empty-detail">Select a job to see its match breakdown.</div>
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
				</div>
				<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
					<a class="btn sm primary" href={`/jobs/${j.id}`}>Open full page →</a>
					<button type="button" class="btn sm" onclick={() => draftCart.toggle(j.id)}>
						{draftCart.has(j.id) ? '✓ In draft list' : '+ Add to draft list'}
					</button>
					<a class="d-link" href={j.url} target="_blank" rel="noopener">
						View original <Icon name="external" size={12} stroke={2} />
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
						<div class="card-h"><h3>Details</h3></div>
						<div class="card-b">
							<div class="meta-table">
								<div class="d-meta-row"><span class="dm-k">Source</span><span class="dm-v">{si.label}</span></div>
								<div class="d-meta-row"><span class="dm-k">Remote</span><span class="dm-v">{j.remote ? 'yes' : 'no'}</span></div>
								{#if j.employment_type}<div class="d-meta-row"><span class="dm-k">Type</span><span class="dm-v">{j.employment_type}</span></div>{/if}
								<div class="d-meta-row"><span class="dm-k">Posted</span><span class="dm-v">{j.posted_at ? relTime(j.posted_at) : '—'}</span></div>
								<div class="d-meta-row"><span class="dm-k">Ingested</span><span class="dm-v">{relTime(j.ingested_at)}</span></div>
							</div>
							{#if j.filter_reason}<p class="banner warn" style="margin-top:12px">{j.filter_reason}</p>{/if}
						</div>
					</div>
				</div>
			</div>
		{/if}
	</div>
</div>

{#if draftCart.ids.length > 0}
	<div class="floater draft-cart" class:stacked={selectedCount > 0}>
		<span class="cart-count">Draft list: {draftCart.ids.length} job{draftCart.ids.length === 1 ? '' : 's'}</span>
		<code class="cart-cmd" title={draftCart.command}>{draftCart.command}</code>
		<button type="button" class="btn sm primary" onclick={copyDraftCommand}>{draftCopied ? 'Copied!' : 'Copy /draft'}</button>
		<button type="button" class="btn sm" onclick={() => draftCart.clear()}>Clear</button>
	</div>
{/if}

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
	.draft-cart {
		border-color: var(--strong);
	}
	.draft-cart.stacked {
		bottom: 5.5rem;
	}
	.cart-count {
		color: var(--strong);
		font-weight: 600;
		white-space: nowrap;
	}
	.cart-cmd {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.2rem 0.45rem;
		color: var(--fg);
		font-family: var(--mono);
		font-size: 0.8rem;
		max-width: 22rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
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
