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
	import { type Job, type TaskSnapshot } from '$lib/api';
	import { pollTask } from '$lib/pollTask';
	import { daysOverdue as overdueDays, fmtDate } from '$lib/date';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let k = $derived(data.kpis);

	const distMax = $derived(Math.max(1, ...data.dist.map((d) => d.n)));
	const sourceMax = $derived(Math.max(1, ...data.bySource.map((s) => s.n)));

	// --- Background actions (scrape + score), owned by the Dashboard. Each runs as
	// a server form action; the client then polls GET /api/ai/tasks/{id}. ---
	const hasProvider = $derived(Boolean(data.aiProvider));
	const pendingCount = $derived(data.pending);

	let ingestTask = $state<TaskSnapshot | null>(null);
	let ingestPolling = $state(false);
	let ingestStarting = $state(false);
	let ingestError = $state<string | null>(null);
	let scrapeForm = $state<HTMLFormElement | null>(null);

	let scoreTask = $state<TaskSnapshot | null>(null);
	let scorePolling = $state(false);
	let scoreStarting = $state(false);
	let scoreError = $state<string | null>(null);
	let scoreForm = $state<HTMLFormElement | null>(null);

	async function pollIngestTask(taskId: string) {
		ingestPolling = true;
		try {
			await pollTask(fetch, data.apiBase ?? '', taskId, (snap) => (ingestTask = snap));
			await invalidateAll();
		} catch (e) {
			ingestError = (e as Error).message;
		} finally {
			ingestPolling = false;
		}
	}

	async function pollScoreTask(taskId: string) {
		scorePolling = true;
		try {
			await pollTask(fetch, data.apiBase ?? '', taskId, (snap) => (scoreTask = snap));
			await invalidateAll();
		} catch (e) {
			scoreError = (e as Error).message;
		} finally {
			scorePolling = false;
		}
	}

	function dismissIngestPanel() {
		ingestTask = null;
		ingestError = null;
	}
	function dismissScorePanel() {
		scoreTask = null;
		scoreError = null;
	}

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
					class="btn"
					disabled={pendingCount === 0 || scoreStarting || scorePolling}
					title={pendingCount === 0
						? 'Nothing to score'
						: `Score ${pendingCount} pending via ${data.aiProvider}`}
				>
					<Icon name="star" size={15} stroke={2} />
					{scoreStarting || scorePolling ? 'Scoring…' : `Score pending (${pendingCount})`}
				</button>
			</form>
		{:else}
			<a class="btn" href="/settings" title="Select an AI CLI in Settings">
				Score pending — set up AI
			</a>
		{/if}

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
				class="btn primary"
				disabled={ingestStarting || ingestPolling}
				title="Pull new postings from every source"
			>
				<Icon name="refresh" size={15} stroke={2} />
				{ingestStarting || ingestPolling ? 'Scraping…' : 'Run scrape'}
			</button>
		</form>
	</div>
</div>

<div class="view-body">
	{#if ingestError && !ingestTask}<p class="panel-err">{ingestError}</p>{/if}
	{#if ingestTask}
		<ScoreProgress
			task={ingestTask}
			onDismiss={dismissIngestPanel}
			runningVerb="Scraping"
			doneVerb="Scraped"
			resultsLabel="sources"
		/>
	{/if}
	{#if scoreError && !scoreTask}<p class="panel-err">{scoreError}</p>{/if}
	{#if scoreTask}<ScoreProgress task={scoreTask} onDismiss={dismissScorePanel} />{/if}

	<div class="kpi-grid">
		<div class="kpi">
			<div class="k-label"><Icon name="queue" size={15} />In queue</div>
			<div class="k-val">{k.jobs}</div>
			<div class="k-delta">{k.unreviewed} unreviewed</div>
		</div>
		<div class="kpi">
			<div class="k-label"><Icon name="star" size={15} />Strong matches</div>
			<div class="k-val" style="color:var(--strong)">{k.strong}</div>
			<div class="k-delta">scored <b>80+</b> against your resume</div>
		</div>
		<div class="kpi">
			<div class="k-label"><Icon name="check" size={15} />Applied</div>
			<div class="k-val">{k.applied}</div>
			<div class="k-delta">{k.rejected} rejected · {k.unreviewed} unset</div>
		</div>
		<div class="kpi">
			<div class="k-label"><Icon name="clock" size={15} />Follow-ups due</div>
			<div class="k-val" style={k.followupsDue > 0 ? 'color:var(--weak)' : ''}>{k.followupsDue}</div>
			<div class="k-delta">applied roles awaiting a nudge</div>
		</div>
		<div class="kpi">
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
								<div class="d-track">
									<div
										class="d-fill"
										style="width:{(d.n / distMax) * 100}%;background:var(--{d.band})"
									></div>
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
								<div class="sl-bar"><div class="sl-fill" style="width:{(s.n / sourceMax) * 100}%"></div></div>
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
						<span class="mi-right">{over === 0 ? 'due today' : `${over}d overdue`}</span>
					</a>
				{:else}
					<p class="empty-line">Nothing overdue. Nice.</p>
				{/each}
			</div>
		</div>
	</div>
</div>

<style>
	.panel-err {
		color: var(--bad);
		font-size: 0.85rem;
		margin: 0 0 1rem;
	}
	.kpi-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(178px, 1fr));
		gap: 12px;
	}
	.kpi {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
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
		height: 9px;
		border-radius: 20px;
		background: var(--surface-2);
		overflow: hidden;
	}
	.dist-row .d-fill {
		height: 100%;
		border-radius: 20px;
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
		height: 7px;
		border-radius: 20px;
		background: var(--surface-2);
		overflow: hidden;
	}
	.src-legend .sl-fill {
		height: 100%;
		background: var(--accent);
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
