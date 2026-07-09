<script lang="ts">
	import { onMount } from 'svelte';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import Icon from '$lib/Icon.svelte';
	import ScoreBadge from '$lib/ScoreBadge.svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import { scoreBandVar } from '$lib/score';
	import { sourceInfo } from '$lib/sources';
	import { onCommand } from '$lib/shell/commandBus';
	import { type Job } from '$lib/api';
	import { createTaskRunner } from '$lib/taskRunner.svelte';
	import { daysOverdue as overdueDays, fmtDate, formatOverdue } from '$lib/date';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let k = $derived(data.kpis);

	const distMax = $derived(Math.max(1, ...data.dist.map((d) => d.n)));
	const sourceMax = $derived(Math.max(1, ...data.bySource.map((s) => s.n)));

	// --- Background actions (scrape + score), owned by the Dashboard. Each runs as
	// a server form action; the client then polls GET /api/ai/tasks/{id}. ---
	const hasProvider = $derived(Boolean(data.aiProvider));
	const pendingCount = $derived(data.pending);

	const ingest = createTaskRunner({
		apiBase: () => data.apiBase ?? '',
		onSettled: () => invalidateAll(),
		failMessage: 'could not start scrape'
	});
	const score = createTaskRunner({
		apiBase: () => data.apiBase ?? '',
		onSettled: () => invalidateAll(),
		failMessage: 'could not start scoring'
	});
	let scrapeForm = $state<HTMLFormElement | null>(null);
	let scoreForm = $state<HTMLFormElement | null>(null);

	// The command palette (Cmd/Ctrl-K) navigates here, then fires the matching action.
	onMount(() =>
		onCommand((name) => {
			if (name === 'scrape') scrapeForm?.requestSubmit();
			else if (name === 'score' && hasProvider && pendingCount > 0) scoreForm?.requestSubmit();
		})
	);


	function openJob(job: Job) {
		goto(`/jobs/${job.id}`);
	}
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>Dashboard</h1>
		<div class="vh-sub">
			<b class="num">{k.jobs}</b> jobs in queue · <b class="num">{k.scored}</b> scored
		</div>
	</div>
	<div class="vh-actions">
		<a class="btn" href="/search">Edit search profile</a>

		{#if hasProvider}
			<form bind:this={scoreForm} method="POST" action="?/scorePending" use:enhance={score.enhance}>
				<button
					type="submit"
					class="btn"
					disabled={pendingCount === 0 || score.busy}
					title={pendingCount === 0
						? 'Nothing to score'
						: `Score ${pendingCount} pending via ${data.aiProvider}`}
				>
					<Icon name="star" size={15} stroke={2} />
					{score.busy ? 'Scoring…' : `Score pending (${pendingCount})`}
				</button>
			</form>
		{:else}
			<a class="btn" href="/settings" title="Select an AI CLI in Settings">
				Score pending — set up AI
			</a>
		{/if}

		<form bind:this={scrapeForm} method="POST" action="?/runIngest" use:enhance={ingest.enhance}>
			<button
				type="submit"
				class="btn primary"
				disabled={ingest.busy}
				title="Pull new postings from every source"
			>
				<Icon name="refresh" size={15} stroke={2} />
				{ingest.busy ? 'Scraping…' : 'Run scrape'}
			</button>
		</form>
	</div>
</div>

<div class="view-body">
	{#if ingest.error && !ingest.snap}<p class="err-text" style="margin-bottom:1rem">{ingest.error}</p>{/if}
	<ScoreProgress
		task={ingest.snap}
		onDismiss={ingest.dismiss}
		runningVerb="Scraping"
		doneVerb="Scraped"
		resultsLabel="sources"
	/>
	{#if score.error && !score.snap}<p class="err-text" style="margin-bottom:1rem">{score.error}</p>{/if}
	<ScoreProgress task={score.snap} onDismiss={score.dismiss} />

	<div class="kpi-grid">
		<div class="card kpi">
			<div class="k-label"><Icon name="queue" size={15} />In queue</div>
			<div class="k-val">{k.jobs}</div>
			<div class="k-delta">{k.unreviewed} unreviewed</div>
		</div>
		<div class="card kpi">
			<div class="k-label"><Icon name="star" size={15} />Strong matches</div>
			<div class="k-val" style="color:var(--strong)">{k.strong}</div>
			<div class="k-delta">scored <b>80+</b> against your resume</div>
		</div>
		<div class="card kpi">
			<div class="k-label"><Icon name="check" size={15} />Applied</div>
			<div class="k-val">{k.applied}</div>
			<div class="k-delta">{k.rejected} rejected · {k.unreviewed} unset</div>
		</div>
		<div class="card kpi">
			<div class="k-label"><Icon name="clock" size={15} />Follow-ups due</div>
			<div class="k-val" style={k.followupsDue > 0 ? 'color:var(--weak)' : ''}>{k.followupsDue}</div>
			<div class="k-delta">applied roles awaiting a nudge</div>
		</div>
		<div class="card kpi">
			<div class="k-label"><Icon name="chart" size={15} />Avg match</div>
			<div class="k-val">{k.avg ?? '—'}<small>/100</small></div>
			<div class="k-delta">across {k.scored} scored roles</div>
		</div>
	</div>

	<div class="dash-grid">
		<div class="card">
			<div class="card-h">
				<h3>Top unreviewed matches</h3>
				<a class="btn ghost sm" style="margin-left:auto" href="/">Open queue →</a>
			</div>
			<div class="card-b" style="padding:8px 12px">
				<div class="mini-list">
					{#each data.topMatches as job (job.id)}
						<button type="button" class="mini-item" onclick={() => openJob(job)}>
							<ScoreBadge score={job.score?.score ?? null} size="sm" />
							<div class="mi-main">
								<div class="mi-title">{job.title}</div>
								<div class="mi-sub">
									{job.company?.name ?? 'Unknown'} · {sourceInfo(job.source).label}
									{#if job.location}· {job.location}{/if}
								</div>
							</div>
							<Icon name="chevron" size={16} stroke={2} />
						</button>
					{:else}
						<p class="empty-line">No unreviewed scored matches. Run a scrape or score the queue.</p>
					{/each}
				</div>
			</div>
		</div>

		<div style="display:flex;flex-direction:column;gap:16px">
			<div class="card">
				<div class="card-h"><h3>Score distribution</h3></div>
				<div class="card-b">
					<div class="dist">
						{#each data.dist as d (d.label)}
							<div class="dist-row">
								<div class="d-band">{d.label}</div>
								<div class="meter d-track">
									<i style="width:{(d.n / distMax) * 100}%;background:var(--{d.band})"></i>
								</div>
								<div class="d-n">{d.n}</div>
							</div>
						{/each}
					</div>
				</div>
			</div>
			<div class="card">
				<div class="card-h"><h3>By source</h3></div>
				<div class="card-b">
					<div class="src-legend">
						{#each data.bySource as s (s.source)}
							<div class="sl-row">
								<span class="pill src-{s.source}"><span class="dot-badge"></span>{sourceInfo(s.source).label}</span>
								<div class="meter sl-bar"><i style="width:{(s.n / sourceMax) * 100}%;background:var(--accent)"></i></div>
								<span class="sl-n">{s.n}</span>
							</div>
						{:else}
							<p class="empty-line">No jobs yet.</p>
						{/each}
					</div>
				</div>
			</div>
		</div>
	</div>

	<div class="card" style="margin-top:16px">
		<div class="card-h">
			<h3>Overdue follow-ups</h3>
			<a class="btn ghost sm" style="margin-left:auto" href="/followups">See all →</a>
		</div>
		<div class="card-b" style="padding:8px 12px">
			<div class="mini-list">
				{#each data.followups as job (job.id)}
					{@const over = overdueDays(job.application?.next_followup_at)}
					<a class="mini-item" href={`/jobs/${job.id}`}>
						<div class="mi-main">
							<div class="mi-title">{job.title}</div>
							<div class="mi-sub">
								{job.company?.name ?? 'Unknown'} · applied {fmtDate(job.application?.applied_at)}
							</div>
						</div>
						<span class="mi-right">{formatOverdue(over)}</span>
					</a>
				{:else}
					<p class="empty-line">Nothing overdue. Nice.</p>
				{/each}
			</div>
		</div>
	</div>
</div>

<style>
	.kpi-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(178px, 1fr));
		gap: 12px;
	}
	.kpi {
		padding: 15px 16px;
	}
	.kpi .k-label {
		font-size: 11.5px;
		color: var(--muted);
		font-weight: 560;
		display: flex;
		align-items: center;
		gap: 7px;
	}
	.kpi .k-label :global(svg) {
		color: var(--faint);
	}
	.kpi .k-val {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
		font-size: 30px;
		font-weight: 640;
		letter-spacing: -0.02em;
		margin-top: 8px;
		line-height: 1;
	}
	.kpi .k-val small {
		font-size: 14px;
		color: var(--faint);
		font-weight: 500;
	}
	.kpi .k-delta {
		font-size: 11.5px;
		margin-top: 6px;
		color: var(--faint);
	}
	.kpi .k-delta :global(b) {
		color: var(--strong);
		font-weight: 600;
	}
	.dash-grid {
		display: grid;
		grid-template-columns: 1.5fr 1fr;
		gap: 16px;
		margin-top: 16px;
	}
	@media (max-width: 1080px) {
		.dash-grid {
			grid-template-columns: 1fr;
		}
	}
	.mini-list {
		display: flex;
		flex-direction: column;
	}
	.mini-item {
		display: flex;
		align-items: center;
		gap: 11px;
		padding: 10px 4px;
		border-bottom: 1px solid var(--border);
		cursor: pointer;
		border-radius: 7px;
		width: 100%;
		text-align: left;
		background: none;
		border-left: 0;
		border-right: 0;
		border-top: 0;
		color: var(--fg);
		text-decoration: none;
	}
	.mini-item:last-child {
		border-bottom: 0;
	}
	.mini-item:hover {
		background: var(--surface-2);
		text-decoration: none;
	}
	.mini-item .mi-main {
		min-width: 0;
		flex: 1;
	}
	.mini-item .mi-title {
		font-weight: 560;
		font-size: 13px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.mini-item .mi-sub {
		font-size: 11.5px;
		color: var(--faint);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.mini-item .mi-right {
		font-size: 11.5px;
		color: var(--weak);
		font-weight: 600;
		white-space: nowrap;
	}
	.mini-item :global(svg) {
		color: var(--faint);
	}
	.dist {
		display: flex;
		flex-direction: column;
		gap: 11px;
	}
	.dist-row {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.dist-row .d-band {
		width: 64px;
		font-size: 12px;
		color: var(--muted);
		font-weight: 560;
	}
	.dist-row .d-track {
		flex: 1;
		--meter-h: 9px;
	}
	.dist-row .d-n {
		width: 26px;
		text-align: right;
		font-family: var(--mono);
		font-size: 12px;
		color: var(--muted);
	}
	.src-legend {
		display: flex;
		flex-direction: column;
		gap: 9px;
	}
	.src-legend .sl-row {
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 12.5px;
	}
	.src-legend .sl-bar {
		flex: 1;
	}
	.src-legend .sl-n {
		font-family: var(--mono);
		font-size: 12px;
		color: var(--muted);
		width: 24px;
		text-align: right;
	}
	.empty-line {
		color: var(--faint);
		font-size: 12.5px;
		padding: 10px 4px;
	}
</style>
