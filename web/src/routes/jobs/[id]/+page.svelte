<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { PageData } from './$types';
	import { APPLICATION_STATUSES, type ApplicationStatus } from '$lib/api';
	import { fmtDateTime as fmtUpdated, defaultFollowupDate } from '$lib/date';
	import { draftCart } from '$lib/draftCart.svelte';
	import TailoredDraftCard from '$lib/TailoredDraftCard.svelte';
	import ScoreBreakdown from '$lib/ScoreBreakdown.svelte';
	import Icon from '$lib/Icon.svelte';
	import { sourceInfo } from '$lib/sources';

	let { data }: { data: PageData } = $props();

	const statuses = APPLICATION_STATUSES;

	let apiBase = $derived(data.apiBase ?? '');
	let job = $derived(data.job);
	let usedUnemp = $derived(job.application?.used_for_unemployment ?? false);
	let draft = $derived(data.draft);
	let scoreHistory = $derived(data.scoreHistory);
	let canonical = $derived(data.canonical);
	let si = $derived(sourceInfo(job.source));

	const hasProvider = $derived(Boolean(data.aiProvider));

	let pendingStatus = $state<ApplicationStatus>('new');
	let followupInput = $state<string>('');
	function followupDefault(): string {
		const existing = job.application?.next_followup_at;
		if (existing) return new Date(existing).toISOString().slice(0, 10);
		return defaultFollowupDate();
	}
	$effect(() => {
		pendingStatus = job.application?.status ?? 'new';
		followupInput = followupDefault();
	});
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>{job.title}</h1>
		<div class="vh-sub d-org">
			<span class="pill src-{job.source}"><span class="dot-badge"></span>{si.label}</span>
			<b style="color:var(--fg)">{job.company?.name ?? 'Unknown'}</b>
			{#if job.location}· {job.location}{/if}
			{#if job.employment_type}· {job.employment_type}{/if}
		</div>
	</div>
	<div class="vh-actions">
		<a class="btn ghost" href="/">← Queue</a>
		<button
			type="button"
			class="btn"
			class:primary={draftCart.has(job.id)}
			onclick={() => draftCart.toggle(job.id)}
			title="Queue this job for a /draft command"
		>
			{draftCart.has(job.id) ? '✓ In draft list' : '+ Add to draft list'}
		</button>
	</div>
</div>

<div class="view-body">
	<div class="stack" style="max-width:900px">
		{#if job.duplicate_of != null}
			<p class="banner warn">
				<strong>Duplicate</strong> of
				<a href={`/jobs/${job.duplicate_of}`}>
					#{job.duplicate_of}{#if canonical} — {canonical.title} at {canonical.company?.name ?? 'Unknown'}{/if}
				</a>
			</p>
		{/if}

		<a class="d-link" href={job.url} target="_blank" rel="noopener">
			View original posting <Icon name="external" size={12} stroke={2} />
		</a>

		<div class="grid-2">
			<div class="card">
				<div class="card-h">
					<h2>Match score</h2>
					{#if job.score}<span class="tag" style="margin-left:auto">{job.score.score_kind}</span>{/if}
				</div>
				<div class="card-b">
					{#if job.score}
						<ScoreBreakdown score={job.score} />
						{#if scoreHistory.length > 0}
							<details class="det"><summary>Previous scores ({scoreHistory.length})</summary>
								<ul class="history-list">
									{#each scoreHistory as h, i (i)}
										<li>
											<details>
												<summary>
													<span class="h-score">{h.score}/100</span>
													<span class="tag">{h.score_kind}</span>
													· <span>{fmtUpdated(h.scored_at)}</span>
													{#if h.resume_filename}· <span class="mono">{h.resume_filename}</span>{/if}
												</summary>
												{#if h.reasoning}<p class="rationale">{h.reasoning}</p>{/if}
												{#if h.rubric && Object.keys(h.rubric).length > 0}<pre>{JSON.stringify(h.rubric, null, 2)}</pre>{/if}
											</details>
										</li>
									{/each}
								</ul>
							</details>
						{/if}
					{:else}
						<p class="muted">Not yet scored. Run <code>/match-pending</code> or the Score-pending button on the queue.</p>
					{/if}
				</div>
			</div>

			<div class="card">
				<div class="card-h"><h2>Status &amp; tracking</h2></div>
				<div class="card-b">
					<form method="POST" action="?/setStatus" class="row-form">
						<select class="input" name="status" bind:value={pendingStatus} style="flex:1;min-width:9rem">
							{#each statuses as s (s)}<option value={s}>{s}</option>{/each}
						</select>
						{#if pendingStatus === 'applied'}
							<input class="input" type="date" name="next_followup_at" bind:value={followupInput} style="width:auto" />
						{/if}
						<button type="submit" class="btn">Update</button>
					</form>
					{#if job.application?.next_followup_at}
						<p class="muted small">
							Next follow-up: {new Date(job.application.next_followup_at).toLocaleDateString()}
							{#if job.application.last_contact_at}· last contact {new Date(job.application.last_contact_at).toLocaleDateString()}{/if}
							{#if job.application.outcome}· outcome: <strong>{job.application.outcome}</strong>{/if}
						</p>
					{/if}

					<div class="field-label">Unemployment claim</div>
					<form method="POST" action="?/setUnemployment" class="row-form">
						<input type="hidden" name="used" value={usedUnemp ? 'false' : 'true'} />
						<button type="submit" class="btn" class:primary={usedUnemp}>
							{usedUnemp ? '✓ Used for unemployment' : 'Mark used for unemployment'}
						</button>
						{#if usedUnemp && job.application?.used_for_unemployment_at}
							<span class="muted small">marked {new Date(job.application.used_for_unemployment_at).toLocaleDateString()}</span>
						{/if}
					</form>

					<div class="field-label">Notes</div>
					<form method="POST" action="?/setNotes">
						<textarea class="input" name="notes" rows="4" placeholder="Personal notes about this role…">{job.application?.notes ?? ''}</textarea>
						<button type="submit" class="btn sm" style="margin-top:8px">Save notes</button>
					</form>
				</div>
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>Tailored draft</h2></div>
			<div class="card-b">
				<TailoredDraftCard
					jobId={job.id}
					{draft}
					{hasProvider}
					{apiBase}
					onDraftChange={() => invalidateAll()}
				/>
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>Description</h2></div>
			<div class="card-b description">
				<!-- eslint-disable-next-line svelte/no-at-html-tags -->
				{@html job.description}
			</div>
		</div>
	</div>
</div>

<style>
	.d-org {
		display: flex;
		align-items: center;
		gap: 9px;
		flex-wrap: wrap;
	}
	.rationale {
		color: var(--muted);
		font-size: 13px;
		line-height: 1.6;
		margin-top: 12px;
	}
	.det {
		margin-top: 14px;
		border-top: 1px solid var(--border);
		padding-top: 12px;
	}
	.det summary {
		cursor: pointer;
		font-size: 12px;
		font-weight: 600;
		color: var(--accent);
	}
	.history-list {
		list-style: none;
		padding: 0;
		margin: 10px 0 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.history-list li {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 8px 10px;
	}
	.history-list summary {
		cursor: pointer;
		font-size: 12px;
		color: var(--muted);
		display: flex;
		gap: 6px;
		align-items: center;
		flex-wrap: wrap;
	}
	.h-score {
		font-weight: 600;
		color: var(--fg);
		font-variant-numeric: tabular-nums;
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
	}
	pre {
		background: var(--bg);
		border: 1px solid var(--border);
		padding: 0.75rem;
		border-radius: 8px;
		overflow-x: auto;
		font-size: 12px;
		margin-top: 8px;
	}
	.description :global(*) {
		color: var(--fg) !important;
		background-color: transparent !important;
	}
	.description :global(a) {
		color: var(--accent) !important;
	}
	.description :global(p) {
		line-height: 1.6;
	}
	.description :global(ul),
	.description :global(ol) {
		padding-left: 1.5rem;
	}
	.description :global(img) {
		max-width: 100%;
		height: auto;
	}
	.description :global(pre),
	.description :global(code) {
		background: var(--bg) !important;
	}
</style>
