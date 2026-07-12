<script lang="ts">
	// Master-detail reading pane for the Queue. Given the selected list `job`, it
	// lazy-loads the full posting (description) + tailored draft so triage happens
	// without leaving the list — score/status come from the list payload (instant),
	// description + draft are fetched per selection. Extracted from the Queue page's
	// +page.svelte to keep that component to list + bulk-action concerns.
	import { invalidateAll } from '$app/navigation';
	import { api, type Draft, type Job, type JobDetail } from '$lib/api';
	import { relTime } from '$lib/date';
	import { draftCart } from '$lib/draftCart.svelte';
	import Icon from '$lib/Icon.svelte';
	import JobDescription from '$lib/JobDescription.svelte';
	import ScoreBreakdown from '$lib/ScoreBreakdown.svelte';
	import StatusTrackingCard from '$lib/StatusTrackingCard.svelte';
	import TailoredDraftCard from '$lib/TailoredDraftCard.svelte';
	import { sourceInfo } from '$lib/sources';

	let {
		job,
		hasProvider,
		apiBase
	}: { job: Job | null; hasProvider: boolean; apiBase: string } = $props();

	let detailJob = $state<JobDetail | null>(null);
	let detailDraft = $state<Draft | null>(null);
	let detailErr = $state<string | null>(null);

	$effect(() => {
		const id = job?.id ?? null;
		if (id == null) {
			detailJob = null;
			detailDraft = null;
			detailErr = null;
			return;
		}
		let cancelled = false;
		detailErr = null;
		detailJob = null;
		detailDraft = null;
		(async () => {
			try {
				const [jd, dr] = await Promise.all([
					api.getJob(fetch, apiBase, id),
					api.getDraft(fetch, apiBase, id)
				]);
				if (!cancelled) {
					detailJob = jd;
					detailDraft = dr;
				}
			} catch (e) {
				if (!cancelled) detailErr = (e as Error).message;
			}
		})();
		return () => {
			cancelled = true;
		};
	});

	async function reloadDraft(id: number) {
		try {
			detailDraft = await api.getDraft(fetch, apiBase, id);
		} catch {
			/* keep the stale draft rather than blanking the panel */
		}
	}
</script>

<div class="detail">
	{#if !job}
		<div class="empty-detail">Select a job to see its full posting and take action.</div>
	{:else}
		{@const j = job}
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
				<button
					type="button"
					class="btn sm"
					class:primary={draftCart.has(j.id)}
					onclick={() => draftCart.toggle(j.id)}
				>
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
							<div class="draft-empty">Not scored yet. Use the Score-pending button on the dashboard.</div>
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
							{apiBase}
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

<style>
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
	.d-actions {
		margin-top: 12px;
		display: flex;
		gap: 10px;
		align-items: center;
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
</style>
