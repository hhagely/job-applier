<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/stores';
	import { api, type ApplicationStatus, type Job, type TaskSnapshot } from '$lib/api';
	import { draftCart } from '$lib/draftCart.svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// --- In-app scoring (Phase 4) ---
	// N = jobs on this queue that are unscored or scored against an older resume.
	const pendingCount = $derived(
		data.jobs.filter((j) => j.score == null || j.score?.is_stale).length
	);
	const hasProvider = $derived(Boolean(data.aiProvider));
	const showScoreButton = $derived(data.filter_status === 'passed');

	let scoreTask = $state<TaskSnapshot | null>(null);
	let scorePolling = $state(false);
	let scoreStarting = $state(false);
	let scoreError = $state<string | null>(null);

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
			// New scores landed (and low scorers were archived) — refresh the board.
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

	type SortKey = 'score-desc' | 'score-asc' | 'posted-desc' | 'ingested-desc' | 'title-asc';
	type StatusFilter = ApplicationStatus | 'none';
	type Ease = 'easy' | 'med' | 'hard';

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

	async function copyDraftCommand() {
		try {
			await navigator.clipboard.writeText(draftCart.command);
			draftCopied = true;
			if (draftCopyTimer) clearTimeout(draftCopyTimer);
			draftCopyTimer = setTimeout(() => {
				draftCopied = false;
			}, 1500);
		} catch {
			// clipboard write failed (e.g., insecure context) — leave state false
		}
	}

	async function copySelectedIds() {
		const ids = [...selected].sort((a, b) => a - b).join(' ');
		try {
			await navigator.clipboard.writeText(ids);
			copied = true;
			if (copyTimer) clearTimeout(copyTimer);
			copyTimer = setTimeout(() => {
				copied = false;
			}, 1500);
		} catch {
			// clipboard write failed (e.g., insecure context) — leave copied false
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

	function scoreBucket(score: number | undefined | null): string {
		if (score == null) return 'none';
		if (score >= 80) return 'high';
		if (score >= 60) return 'med';
		return 'low';
	}

	const SOURCE_META: Record<string, { label: string; ease: Ease }> = {
		greenhouse: { label: 'Greenhouse', ease: 'easy' },
		lever: { label: 'Lever', ease: 'easy' },
		ashby: { label: 'Ashby', ease: 'easy' },
		remoteok: { label: 'RemoteOK', ease: 'med' },
		weworkremotely: { label: 'WWR', ease: 'med' },
		workday: { label: 'Workday', ease: 'hard' },
		hackernews: { label: 'HN', ease: 'med' }
	};

	function sourceInfo(source: string): { label: string; ease: Ease } {
		return SOURCE_META[source] ?? { label: source, ease: 'med' };
	}

	function jobStatusKey(job: Job): StatusFilter {
		return job.application?.status ?? 'none';
	}

	function toggleStatus(key: StatusFilter) {
		const next = new Set(activeStatuses);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		activeStatuses = next;
	}

	function toggleEase(key: Ease) {
		const next = new Set(activeEases);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		activeEases = next;
	}

	function toggleSource(key: string) {
		const next = new Set(activeSources);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		activeSources = next;
	}

	function toggleUnemp(key: UnempFilter) {
		const next = new Set(activeUnemp);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		activeUnemp = next;
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

	const visible = $derived.by(() => {
		let list = data.jobs.slice();
		if (activeStatuses.size > 0) {
			list = list.filter((j) => activeStatuses.has(jobStatusKey(j)));
		}
		if (activeEases.size > 0) {
			list = list.filter((j) => activeEases.has(sourceInfo(j.source).ease));
		}
		if (activeSources.size > 0) {
			list = list.filter((j) => activeSources.has(j.source));
		}
		if (unscoredOnly) {
			list = list.filter((j) => j.score == null);
		}
		if (activeUnemp.size > 0) {
			list = list.filter((j) =>
				activeUnemp.has(isUsedForUnemployment(j) ? 'used' : 'unused')
			);
		}
		if (minScore !== null) {
			list = list.filter((j) => (j.score?.score ?? -1) >= minScore);
		}
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

	function dateVal(iso: string | null | undefined): number {
		return iso ? new Date(iso).getTime() : 0;
	}

	const allVisibleSelected = $derived(
		visible.length > 0 && visible.every((j) => selected.has(j.id))
	);
	const someVisibleSelected = $derived(visible.some((j) => selected.has(j.id)));

	function toggleSelect(id: number) {
		const next = new Set(selected);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		selected = next;
	}

	function toggleSelectAllVisible() {
		const next = new Set(selected);
		if (allVisibleSelected) {
			for (const j of visible) next.delete(j.id);
		} else {
			for (const j of visible) next.add(j.id);
		}
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
		return data.jobs.filter(
			(j) => isUsedForUnemployment(j) === (key === 'used')
		).length;
	}

	const SOURCE_FILTERS = Object.keys(SOURCE_META);

	let openRubricFor = $state<number | null>(null);

	function toggleRubric(e: MouseEvent, id: number) {
		e.preventDefault();
		e.stopPropagation();
		openRubricFor = openRubricFor === id ? null : id;
	}

	function closeRubric() {
		openRubricFor = null;
	}

	function rubricEntries(rubric: Record<string, unknown> | undefined): [string, unknown][] {
		if (!rubric) return [];
		return Object.entries(rubric);
	}

	function isFollowupDue(job: Job): boolean {
		const due = job.application?.next_followup_at;
		if (!due) return false;
		if (job.application?.outcome) return false;
		return new Date(due).getTime() <= Date.now();
	}

	function defaultFollowupDate(): string {
		const d = new Date(Date.now() + 7 * 86_400_000);
		return d.toISOString().slice(0, 10);
	}

	let followupDate = $state<string>(defaultFollowupDate());

	function toggleDuplicates() {
		const url = new URL($page.url);
		if (data.include_duplicates) {
			url.searchParams.delete('duplicates');
		} else {
			url.searchParams.set('duplicates', '1');
		}
		goto(url, { invalidateAll: true });
	}
</script>

<svelte:window onclick={closeRubric} />

<section class="header-row">
	<h1>
		{data.filter_status === 'passed' ? 'Queue' : 'Manual review'}
	</h1>
	<span class="count">
		{visible.length}
		{visible.length === data.jobs.length ? '' : `of ${data.jobs.length}`} jobs
	</span>

	{#if showScoreButton}
		<div class="score-pending">
			{#if !hasProvider}
				<a class="score-btn disabled" href="/settings" title="Select an AI CLI in Settings">
					Score pending — set up AI
				</a>
			{:else}
				<form
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
						class="score-btn"
						disabled={pendingCount === 0 || scoreStarting || scorePolling}
						title={pendingCount === 0 ? 'Nothing to score' : `Score ${pendingCount} pending via ${data.aiProvider}`}
					>
						{#if scoreStarting || scorePolling}
							Scoring…
						{:else}
							Score pending ({pendingCount})
						{/if}
					</button>
				</form>
			{/if}
		</div>
	{/if}
</section>

{#if scoreError && !scoreTask}
	<p class="score-error">{scoreError}</p>
{/if}

{#if scoreTask}
	<ScoreProgress task={scoreTask} onDismiss={dismissScorePanel} />
{/if}

<section class="toolbar">
	<div class="toolbar-row">
		<label class="sort">
			Sort
			<select bind:value={sortBy}>
				<option value="score-desc">Score (high → low)</option>
				<option value="score-asc">Score (low → high)</option>
				<option value="posted-desc">Posted (newest)</option>
				<option value="ingested-desc">Ingested (newest)</option>
				<option value="title-asc">Title (A → Z)</option>
			</select>
		</label>

		<label class="num">
			Min score
			<input
				type="number"
				min="0"
				max="100"
				placeholder="any"
				bind:value={minScoreInput}
			/>
		</label>

		<label class="check">
			<input type="checkbox" bind:checked={unscoredOnly} />
			Unscored only
		</label>

		<label class="check">
			<input
				type="checkbox"
				checked={data.include_duplicates}
				onchange={toggleDuplicates}
			/>
			Show duplicates
		</label>

		{#if activeStatuses.size > 0 || activeEases.size > 0 || activeSources.size > 0 || activeUnemp.size > 0 || unscoredOnly || minScoreInput !== ''}
			<button type="button" class="clear" onclick={clearFilters}>Clear filters</button>
		{/if}
	</div>

	<div class="chips-row">
		<span class="chips-label">Ease</span>
		<div class="chips">
			{#each EASE_FILTERS as f (f.key)}
				{@const n = easeCount(f.key)}
				{#if n > 0}
					<button
						type="button"
						class="chip"
						data-ease={f.key}
						class:active={activeEases.has(f.key)}
						onclick={() => toggleEase(f.key)}
					>
						{f.label}
						<span class="chip-count">{n}</span>
					</button>
				{/if}
			{/each}
		</div>
	</div>

	<div class="chips-row">
		<span class="chips-label">Source</span>
		<div class="chips">
			{#each SOURCE_FILTERS as key (key)}
				{@const n = sourceCount(key)}
				{#if n > 0}
					{@const meta = SOURCE_META[key]}
					<button
						type="button"
						class="chip"
						data-ease={meta.ease}
						class:active={activeSources.has(key)}
						onclick={() => toggleSource(key)}
					>
						{meta.label}
						<span class="chip-count">{n}</span>
					</button>
				{/if}
			{/each}
		</div>
	</div>

	<div class="chips-row">
		<span class="chips-label">Status</span>
		<div class="chips">
			{#each STATUS_FILTERS as f (f.key)}
				{@const n = statusCount(f.key)}
				{#if n > 0}
					<button
						type="button"
						class="chip"
						class:active={activeStatuses.has(f.key)}
						onclick={() => toggleStatus(f.key)}
					>
						{f.label}
						<span class="chip-count">{n}</span>
					</button>
				{/if}
			{/each}
		</div>
	</div>

		<div class="chips-row">
			<span class="chips-label">Unemp.</span>
			<div class="chips">
				{#each UNEMP_FILTERS as f (f.key)}
					<button
						type="button"
						class="chip"
						class:active={activeUnemp.has(f.key)}
						onclick={() => toggleUnemp(f.key)}
					>
						{f.label}
						<span class="chip-count">{unempCount(f.key)}</span>
					</button>
				{/each}
			</div>
		</div>
	</section>

{#if data.jobs.length === 0}
	<p class="empty">
		Nothing here. Run <code>make ingest</code> to pull new postings.
	</p>
{:else if visible.length === 0}
	<p class="empty">No jobs match the current filters.</p>
{:else}
	<div class="select-all">
		<label>
			<input
				type="checkbox"
				checked={allVisibleSelected}
				indeterminate={!allVisibleSelected && someVisibleSelected}
				onchange={toggleSelectAllVisible}
			/>
			Select all visible ({visible.length})
		</label>
	</div>

	<ul class="jobs">
		{#each visible as job (job.id)}
			{@const si = sourceInfo(job.source)}
			<li
				class="row"
				class:selected={selected.has(job.id)}
				class:duplicate={job.duplicate_of != null}
				class:used-unemp={isUsedForUnemployment(job)}
			>
				<label class="check-cell" aria-label="select job">
					<input
						type="checkbox"
						checked={selected.has(job.id)}
						onchange={() => toggleSelect(job.id)}
					/>
				</label>
				<a href={`/jobs/${job.id}`} class="row-link">
					<span class="score-cell">
						{#if job.score}
							<button
								type="button"
								class="score-pill score-pill-btn"
								class:stale={job.score.is_stale}
								data-score={scoreBucket(job.score.score)}
								onclick={(e) => toggleRubric(e, job.id)}
								aria-expanded={openRubricFor === job.id}
								aria-label={job.score.is_stale
									? 'Stale score — older resume. Click for rubric.'
									: 'Show rubric breakdown'}
								title={job.score.is_stale
									? 'Scored against an older resume — run /match-pending to refresh'
									: undefined}
							>
								{job.score.score}
								{#if job.score.is_stale}
									<span class="stale-label">stale</span>
								{/if}
							</button>
							{#if openRubricFor === job.id}
								{@const entries = rubricEntries(job.score.rubric)}
								<div
									class="rubric-popover"
									role="dialog"
									tabindex="-1"
									onclick={(e) => e.stopPropagation()}
									onkeydown={(e) => e.key === 'Escape' && closeRubric()}
								>
									<p class="rubric-head">
										<span class="rubric-score">{job.score.score}/100</span>
										<span class="kind" data-kind={job.score.score_kind}>
											{job.score.score_kind}
										</span>
									</p>
									{#if entries.length === 0}
										<p class="rubric-empty">No rubric recorded.</p>
									{:else}
										<ul class="rubric-list">
											{#each entries as [bucket, value] (bucket)}
												<li>
													<span class="rubric-bucket">{bucket}</span>
													<span class="rubric-value">
														{#if typeof value === 'object' && value !== null}
															{JSON.stringify(value)}
														{:else}
															{String(value)}
														{/if}
													</span>
												</li>
											{/each}
										</ul>
									{/if}
									{#if job.score.reasoning}
										<p class="rubric-reasoning">{job.score.reasoning}</p>
									{/if}
								</div>
							{/if}
						{:else}
							<span class="score-pill" data-score="none">—</span>
						{/if}
					</span>
					<span class="main">
						<span class="title">{job.title}</span>
						<span class="meta">
							{#if job.duplicate_of != null}
								<span class="dup-label" title="JD-similarity duplicate">
									dup of #{job.duplicate_of}
								</span>
							{/if}
							{#if job.application}
								<span class="status status-{job.application.status}">
									{job.application.status}
								</span>
							{/if}
							{#if isFollowupDue(job)}
								<span class="followup-chip" title="Follow-up due">⏰ follow-up due</span>
							{/if}
							{#if isUsedForUnemployment(job)}
								<span class="unemp-chip" title="Used for an unemployment claim">
									✓ unemployment
								</span>
							{/if}
							<span
								class="source"
								data-ease={si.ease}
								title="Apply friction: {si.ease}"
							>
								{si.label}
							</span>
							<span class="company">{job.company?.name ?? 'Unknown'}</span>
							{#if job.location}
								<span class="dot">·</span><span>{job.location}</span>
							{/if}
							<span class="dot">·</span>
							<span>
								{#if job.posted_at}
									posted {relTime(job.posted_at)}
								{:else}
									posted ?
								{/if}
								· ingested {relTime(job.ingested_at)}
							</span>
						</span>
						{#if job.filter_reason}
							<span class="reason">{job.filter_reason}</span>
						{/if}
					</span>
				</a>
			</li>
		{/each}
	</ul>
{/if}

{#if draftCart.ids.length > 0}
	<div class="draft-cart-bar" class:stacked={selectedCount > 0}>
		<span class="cart-count">
			Draft list: {draftCart.ids.length} job{draftCart.ids.length === 1 ? '' : 's'}
		</span>
		<code class="cart-cmd" title={draftCart.command}>{draftCart.command}</code>
		<button type="button" class="cart-copy" onclick={copyDraftCommand}>
			{draftCopied ? 'Copied!' : 'Copy /draft command'}
		</button>
		<button type="button" class="cart-clear" onclick={() => draftCart.clear()}>Clear</button>
	</div>
{/if}

{#if selectedCount > 0}
	<form
		method="POST"
		action="?/bulkStatus"
		class="action-bar"
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
		<span class="action-count">
			{selectedCount} selected
		</span>
		{#each [...selected] as id (id)}
			<input type="hidden" name="ids" value={id} />
		{/each}
		<label class="action-status">
			Set status
			<select name="status" bind:value={bulkStatus}>
				{#each BULK_STATUSES as s (s)}
					<option value={s}>{s}</option>
				{/each}
			</select>
		</label>
		{#if bulkStatus === 'applied'}
			<label class="action-status">
				Follow up
				<input type="date" name="next_followup_at" bind:value={followupDate} />
			</label>
		{/if}
		<button type="submit" class="action-apply" disabled={submitting}>
			{submitting ? 'Applying…' : 'Apply'}
		</button>
		<span class="action-sep" aria-hidden="true"></span>
		<button
			type="submit"
			formaction="?/bulkUnemployment"
			name="used"
			value="true"
			class="action-unemp"
			disabled={submitting}
			title="Mark selected as used for an unemployment claim"
		>
			✓ Used for unemployment
		</button>
		<button
			type="submit"
			formaction="?/bulkUnemployment"
			name="used"
			value="false"
			class="action-unemp off"
			disabled={submitting}
			title="Clear the unemployment flag on selected"
		>
			Unmark
		</button>
		<button type="button" class="action-copy" onclick={copySelectedIds}>
			{copied ? 'Copied!' : 'Copy IDs'}
		</button>
		<button type="button" class="action-clear" onclick={clearSelection}>Clear</button>
	</form>
{/if}

<style>
	.header-row {
		display: flex;
		align-items: baseline;
		gap: 1rem;
		margin-bottom: 0.75rem;
	}
	.score-pending {
		margin-left: auto;
	}
	.score-btn {
		background: var(--accent);
		color: #0d1117;
		border: 0;
		border-radius: 6px;
		padding: 0.4rem 0.85rem;
		font-weight: 600;
		font-size: 0.85rem;
		cursor: pointer;
	}
	.score-btn:hover:not(:disabled) {
		filter: brightness(1.1);
	}
	.score-btn:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.score-btn.disabled {
		background: transparent;
		color: var(--warn);
		border: 1px solid var(--warn);
		display: inline-block;
	}
	.score-btn.disabled:hover {
		text-decoration: none;
		filter: brightness(1.1);
	}
	.score-error {
		color: var(--bad);
		margin: 0 0 0.75rem;
		font-size: 0.85rem;
	}
	h1 {
		font-size: 1.4rem;
		margin: 0;
	}
	.count {
		color: var(--muted);
		font-size: 0.9rem;
	}
	.toolbar {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin-bottom: 1rem;
		padding: 0.75rem 0.85rem;
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
	}
	.toolbar-row {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		align-items: center;
	}
	.toolbar label {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.85rem;
		color: var(--muted);
	}
	.toolbar select,
	.toolbar input[type='number'] {
		background: #20262d;
		color: var(--fg);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.25rem 0.4rem;
		font-size: 0.85rem;
	}
	.toolbar input[type='number'] {
		width: 5rem;
	}
	.clear {
		background: transparent;
		color: var(--muted);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.25rem 0.6rem;
		font-size: 0.8rem;
		cursor: pointer;
	}
	.clear:hover {
		color: var(--fg);
		border-color: var(--accent);
	}
	.chips-row {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
	}
	.chips-label {
		font-size: 0.75rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
		min-width: 3.5rem;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}
	.chip {
		background: #20262d;
		color: var(--muted);
		border: 1px solid var(--panel-border);
		border-radius: 999px;
		padding: 0.2rem 0.6rem;
		font-size: 0.78rem;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
	.chip:hover {
		border-color: var(--accent);
	}
	.chip.active {
		background: rgba(88, 166, 255, 0.18);
		color: var(--accent);
		border-color: var(--accent);
	}
	.chip[data-ease='easy'].active {
		background: rgba(46, 160, 67, 0.2);
		color: var(--ok);
		border-color: var(--ok);
	}
	.chip[data-ease='med'].active {
		background: rgba(210, 153, 34, 0.2);
		color: var(--warn);
		border-color: var(--warn);
	}
	.chip[data-ease='hard'].active {
		background: rgba(248, 81, 73, 0.18);
		color: var(--bad);
		border-color: var(--bad);
	}
	.chip-count {
		font-variant-numeric: tabular-nums;
		font-size: 0.7rem;
		opacity: 0.7;
	}
	.empty {
		color: var(--muted);
		padding: 2rem;
		text-align: center;
		border: 1px dashed var(--panel-border);
		border-radius: 8px;
	}
	.select-all {
		font-size: 0.8rem;
		color: var(--muted);
		margin: 0 0 0.5rem 0.4rem;
	}
	.select-all label {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		cursor: pointer;
	}
	.jobs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.row {
		display: flex;
		align-items: stretch;
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
	}
	.row.selected {
		border-color: var(--accent);
		background: rgba(88, 166, 255, 0.06);
	}
	.row.used-unemp {
		border-left: 3px solid var(--ok);
	}
	.row.duplicate {
		opacity: 0.55;
	}
	.row.duplicate:hover {
		opacity: 1;
	}
	.dup-label {
		font-size: 0.7rem;
		letter-spacing: 0.02em;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: rgba(210, 153, 34, 0.18);
		color: var(--warn);
	}
	.row:hover {
		border-color: var(--accent);
	}
	.check-cell {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0 0.6rem 0 0.85rem;
		cursor: pointer;
	}
	.check-cell input {
		width: 1rem;
		height: 1rem;
		cursor: pointer;
	}
	.row-link {
		flex: 1;
		display: flex;
		gap: 1rem;
		padding: 0.85rem 1rem 0.85rem 0.4rem;
		color: var(--fg);
		min-width: 0;
	}
	.row-link:hover {
		text-decoration: none;
	}
	.score-cell {
		position: relative;
		flex: 0 0 3rem;
		align-self: center;
	}
	.score-pill {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 3rem;
		min-height: 2.25rem;
		font-weight: 700;
		padding: 0.4rem 0.5rem;
		border-radius: 6px;
		background: #20262d;
		color: var(--muted);
		font-variant-numeric: tabular-nums;
		line-height: 1;
		box-sizing: border-box;
	}
	.score-pill-btn {
		border: 1px solid transparent;
		cursor: pointer;
		font: inherit;
	}
	.score-pill-btn:hover {
		border-color: var(--accent);
	}
	.rubric-popover {
		position: absolute;
		top: calc(100% + 0.4rem);
		left: 0;
		z-index: 20;
		min-width: 16rem;
		max-width: 22rem;
		background: var(--panel);
		border: 1px solid var(--accent);
		border-radius: 8px;
		padding: 0.6rem 0.75rem;
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
		font-size: 0.8rem;
		color: var(--fg);
		text-align: left;
	}
	.rubric-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.rubric-list li {
		display: flex;
		justify-content: space-between;
		gap: 0.6rem;
	}
	.rubric-bucket {
		color: var(--muted);
		text-transform: lowercase;
	}
	.rubric-value {
		font-variant-numeric: tabular-nums;
	}
	.rubric-reasoning {
		margin: 0.5rem 0 0;
		padding-top: 0.5rem;
		border-top: 1px solid var(--panel-border);
		color: var(--muted);
		font-style: italic;
	}
	.rubric-empty {
		margin: 0;
		color: var(--muted);
	}
	.rubric-head {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0 0 0.45rem;
		padding-bottom: 0.4rem;
		border-bottom: 1px solid var(--panel-border);
	}
	.rubric-score {
		font-weight: 600;
		color: var(--fg);
		font-variant-numeric: tabular-nums;
	}
	.kind {
		font-size: 0.7rem;
		letter-spacing: 0.02em;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: #20262d;
		color: var(--muted);
		text-transform: lowercase;
	}
	.kind[data-kind='tailored'] {
		background: rgba(88, 166, 255, 0.18);
		color: var(--accent);
	}
	.kind[data-kind='baseline'] {
		background: rgba(210, 153, 34, 0.18);
		color: var(--warn);
	}
	.score-pill[data-score='high'] {
		background: rgba(46, 160, 67, 0.2);
		color: var(--ok);
	}
	.score-pill[data-score='med'] {
		background: rgba(210, 153, 34, 0.2);
		color: var(--warn);
	}
	.score-pill[data-score='low'] {
		background: rgba(248, 81, 73, 0.18);
		color: var(--bad);
	}
	.score-pill.stale {
		flex-direction: column;
		gap: 0.1rem;
		background: #20262d;
		color: var(--muted);
		opacity: 0.85;
	}
	.stale-label {
		font-size: 0.55rem;
		font-weight: 500;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		opacity: 0.85;
	}
	.main {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		min-width: 0;
	}
	.title {
		font-weight: 600;
		font-size: 1rem;
	}
	.meta {
		font-size: 0.85rem;
		color: var(--muted);
		display: flex;
		gap: 0.4rem;
		align-items: center;
		flex-wrap: wrap;
	}
	.dot {
		color: var(--panel-border);
	}
	.reason {
		font-size: 0.8rem;
		color: var(--warn);
		font-style: italic;
	}
	.status {
		text-transform: uppercase;
		font-size: 0.7rem;
		letter-spacing: 0.05em;
		padding: 0.1rem 0.4rem;
		border-radius: 4px;
		background: #20262d;
	}
	.source {
		font-size: 0.7rem;
		letter-spacing: 0.02em;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: #20262d;
		color: var(--muted);
		border: 1px solid transparent;
		font-variant-numeric: tabular-nums;
	}
	.source[data-ease='easy'] {
		background: rgba(46, 160, 67, 0.18);
		color: var(--ok);
	}
	.source[data-ease='med'] {
		background: rgba(210, 153, 34, 0.18);
		color: var(--warn);
	}
	.source[data-ease='hard'] {
		background: rgba(248, 81, 73, 0.16);
		color: var(--bad);
	}
	.status-applied {
		background: rgba(46, 160, 67, 0.2);
		color: var(--ok);
	}
	.status-screening {
		background: rgba(163, 113, 247, 0.22);
		color: #d2a8ff;
	}
	.status-interviewing {
		background: rgba(57, 208, 216, 0.18);
		color: #56d4dd;
	}
	.followup-chip {
		font-size: 0.7rem;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: rgba(210, 153, 34, 0.2);
		color: var(--warn);
		letter-spacing: 0.02em;
	}
	.unemp-chip {
		font-size: 0.7rem;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: rgba(46, 160, 67, 0.2);
		color: var(--ok);
		letter-spacing: 0.02em;
	}
	.status-rejected {
		background: rgba(248, 81, 73, 0.18);
		color: var(--bad);
	}
	.status-interested,
	.status-drafted {
		background: rgba(88, 166, 255, 0.18);
		color: var(--accent);
	}

	.action-bar {
		position: fixed;
		left: 50%;
		transform: translateX(-50%);
		bottom: 1.25rem;
		display: flex;
		align-items: center;
		gap: 0.85rem;
		padding: 0.6rem 0.9rem;
		background: var(--panel);
		border: 1px solid var(--accent);
		border-radius: 10px;
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
		font-size: 0.85rem;
		z-index: 10;
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
	.action-status select {
		background: #20262d;
		color: var(--fg);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.25rem 0.4rem;
		font-size: 0.85rem;
	}
	.action-apply {
		background: var(--accent);
		color: #0d1117;
		border: 0;
		border-radius: 4px;
		padding: 0.35rem 0.85rem;
		font-weight: 600;
		cursor: pointer;
	}
	.action-apply:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.action-sep {
		width: 1px;
		align-self: stretch;
		background: var(--panel-border);
	}
	.action-unemp {
		background: rgba(46, 160, 67, 0.18);
		color: var(--ok);
		border: 1px solid var(--ok);
		border-radius: 4px;
		padding: 0.35rem 0.7rem;
		font-weight: 600;
		cursor: pointer;
	}
	.action-unemp.off {
		background: transparent;
		color: var(--muted);
		border-color: var(--panel-border);
		font-weight: 400;
	}
	.action-unemp:hover {
		filter: brightness(1.1);
	}
	.action-unemp:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.action-copy,
	.action-clear {
		background: transparent;
		color: var(--muted);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.3rem 0.6rem;
		cursor: pointer;
	}
	.action-copy:hover,
	.action-clear:hover {
		color: var(--fg);
		border-color: var(--accent);
	}

	.draft-cart-bar {
		position: fixed;
		left: 50%;
		transform: translateX(-50%);
		bottom: 1.25rem;
		display: flex;
		align-items: center;
		gap: 0.7rem;
		padding: 0.55rem 0.85rem;
		background: var(--panel);
		border: 1px solid var(--ok);
		border-radius: 10px;
		box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
		font-size: 0.85rem;
		z-index: 11;
		max-width: 90vw;
	}
	.draft-cart-bar.stacked {
		bottom: 5rem;
	}
	.cart-count {
		color: var(--ok);
		font-weight: 600;
		white-space: nowrap;
	}
	.cart-cmd {
		background: var(--bg);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.2rem 0.45rem;
		color: var(--fg);
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
		max-width: 22rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.cart-copy {
		background: var(--ok);
		color: #0d1117;
		border: 0;
		border-radius: 4px;
		padding: 0.35rem 0.8rem;
		font-weight: 600;
		cursor: pointer;
	}
	.cart-copy:hover {
		filter: brightness(1.1);
	}
	.cart-clear {
		background: transparent;
		color: var(--muted);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.3rem 0.6rem;
		cursor: pointer;
	}
	.cart-clear:hover {
		color: var(--fg);
		border-color: var(--accent);
	}
</style>
