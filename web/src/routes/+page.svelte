<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		api,
		type ApplicationStatus,
		type Draft,
		type Job,
		type JobDetail
	} from '$lib/api';
	import { createTaskRunner } from '$lib/taskRunner.svelte';
	import { defaultFollowupDate, relTime } from '$lib/date';
	import { draftCart } from '$lib/draftCart.svelte';
	import { isUsedForUnemployment } from '$lib/jobFilters';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import ScoreBreakdown from '$lib/ScoreBreakdown.svelte';
	import TailoredDraftCard from '$lib/TailoredDraftCard.svelte';
	import StatusTrackingCard from '$lib/StatusTrackingCard.svelte';
	import JobListRow from '$lib/JobListRow.svelte';
	import JobDescription from '$lib/JobDescription.svelte';
	import Icon from '$lib/Icon.svelte';
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
	const draftBatch = createTaskRunner({
		apiBase: () => data.apiBase ?? '',
		onSettled: async (snap) => {
			// Drafting finished — empty the cart so the same jobs aren't re-drafted on
			// the next run. Keep it on error so the user can retry the failed batch.
			if (snap.status === 'done') draftCart.clear();
			await invalidateAll();
		},
		failMessage: 'could not start drafting'
	});

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

	function jobStatusKey(job: Job): StatusFilter {
		return job.application?.status ?? 'none';
	}

	function toggleIn<T>(set: Set<T>, key: T): Set<T> {
		const next = new Set(set);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		return next;
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

	let detailJob = $state<JobDetail | null>(null);
	let detailDraft = $state<Draft | null>(null);
	let detailLoading = $state(false);
	let detailErr = $state<string | null>(null);

	$effect(() => {
		const id = selectedJob?.id ?? null;
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

	// Toggle the checked rows against the cross-route draft cart, then drop the
	// selection — the rows' "in draft" tags and the header count are the feedback.
	// When every selected row is already in the cart the action removes them;
	// otherwise it adds (already-present ids are no-ops via draftCart.add).
	const allSelectedInCart = $derived(
		selected.size > 0 && [...selected].every((id) => draftCart.has(id))
	);

	function toggleSelectedInDraftCart() {
		if (allSelectedInCart) for (const id of selected) draftCart.remove(id);
		else for (const id of selected) draftCart.add(id);
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

	let followupDate = $state<string>(defaultFollowupDate());

	function toggleDuplicates() {
		const url = new URL(page.url);
		if (data.include_duplicates) url.searchParams.delete('duplicates');
		else url.searchParams.set('duplicates', '1');
		goto(url, { invalidateAll: true });
	}

	function switchQueue(manual: boolean) {
		const url = new URL(page.url);
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
			<form method="POST" action="?/draftBatch" use:enhance={draftBatch.enhance}>
				{#each draftCart.ids as id (id)}
					<input type="hidden" name="ids" value={id} />
				{/each}
				<button
					type="submit"
					class="btn primary"
					disabled={draftCart.ids.length === 0 || draftBatch.busy}
					title={draftCart.ids.length === 0
						? 'Add jobs to your draft list first'
						: `Draft ${draftCart.ids.length} job${draftCart.ids.length === 1 ? '' : 's'} via ${data.aiProvider}`}
				>
					<Icon name="doc" size={15} stroke={2} />
					{#if draftBatch.busy}
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

{#if draftBatch.error && !draftBatch.snap}
	<div class="q-panels"><p class="err-text">{draftBatch.error}</p></div>
{/if}
{#if draftBatch.snap}
	<div class="q-panels">
		<ScoreProgress
			task={draftBatch.snap}
			onDismiss={draftBatch.dismiss}
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
					<JobListRow
						{job}
						active={selectedJob?.id === job.id}
						selected={selected.has(job.id)}
						onSelect={() => selectJob(job.id)}
						onToggleSelect={() => toggleSelect(job.id)}
					/>
				{/each}
			{/if}
		</div>

		{#if selectedCount > 0}
			<div class="bulkbar">
				<b>{selectedCount}</b> selected
				<button type="button" class="btn sm primary" style="margin-left:auto" onclick={toggleSelectedInDraftCart} title={allSelectedInCart ? 'Remove the selected jobs from the draft list' : 'Add the selected jobs to the draft list'}>
					<Icon name="doc" size={13} stroke={2} /> {allSelectedInCart ? 'Remove from draft list' : 'Add to draft list'}
				</button>
				<button type="button" class="btn sm" onclick={copySelectedIds}>
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
								<ScoreBreakdown score={j.score} />
							{/if}
						</div>
					</div>

					<div class="card">
						<div class="card-h"><h3>Status &amp; tracking</h3></div>
						<div class="card-b">
							<StatusTrackingCard
								jobId={j.id}
								application={j.application}
								onChange={() => invalidateAll()}
							/>
						</div>
					</div>
				</div>

				<div class="card" style="margin-top:14px">
					<div class="card-h"><h3>Tailored draft</h3></div>
					<div class="card-b">
						{#key j.id}
							<TailoredDraftCard
								jobId={j.id}
								draft={detailDraft}
								{hasProvider}
								apiBase={base}
								onDraftChange={async () => {
									await invalidateAll();
									await reloadDraft(j.id);
								}}
							/>
						{/key}
					</div>
				</div>

				<div class="card" style="margin-top:14px">
					<div class="card-h"><h3>Description</h3></div>
					<div class="card-b">
						{#if detailErr}
							<p class="err-text">Couldn't load the description: {detailErr}</p>
						{:else if detailJob}
							<JobDescription html={detailJob.description} />
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
		<span class="action-sep" aria-hidden="true"></span>
		<button type="button" class="btn sm" onclick={toggleSelectedInDraftCart} title={allSelectedInCart ? 'Remove the selected jobs from the draft list' : 'Add the selected jobs to the draft list'}>
			<Icon name="doc" size={13} stroke={2} /> {allSelectedInCart ? 'Remove from draft list' : 'Add to draft list'}
		</button>
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
		color: var(--muted);
		font-size: 13px;
		margin-top: 8px;
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
