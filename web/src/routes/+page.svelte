<script lang="ts">
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function relTime(iso: string): string {
		const diff = Date.now() - new Date(iso).getTime();
		const days = Math.floor(diff / 86_400_000);
		if (days === 0) return 'today';
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
</script>

<section class="header-row">
	<h1>
		{data.filter_status === 'passed'
			? 'Queue'
			: data.filter_status === 'manual'
				? 'Manual review'
				: 'Dropped'}
	</h1>
	<span class="count">{data.jobs.length} jobs</span>
</section>

{#if data.jobs.length === 0}
	<p class="empty">
		Nothing here. Run <code>make ingest</code> to pull new postings.
	</p>
{:else}
	<ul class="jobs">
		{#each data.jobs as job (job.id)}
			<li>
				<a href={`/jobs/${job.id}`} class="row">
					<span class="score-pill" data-score={scoreBucket(job.score?.score)}>
						{job.score ? job.score.score : '—'}
					</span>
					<span class="main">
						<span class="title">{job.title}</span>
						<span class="meta">
							<span class="company">{job.company?.name ?? 'Unknown'}</span>
							{#if job.location}
								<span class="dot">·</span><span>{job.location}</span>
							{/if}
							<span class="dot">·</span><span>{relTime(job.ingested_at)}</span>
							{#if job.application}
								<span class="dot">·</span>
								<span class="status status-{job.application.status}">
									{job.application.status}
								</span>
							{/if}
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

<style>
	.header-row {
		display: flex;
		align-items: baseline;
		gap: 1rem;
		margin-bottom: 1rem;
	}
	h1 {
		font-size: 1.4rem;
		margin: 0;
	}
	.count {
		color: var(--muted);
		font-size: 0.9rem;
	}
	.empty {
		color: var(--muted);
		padding: 2rem;
		text-align: center;
		border: 1px dashed var(--panel-border);
		border-radius: 8px;
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
		gap: 1rem;
		padding: 0.85rem 1rem;
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
		color: var(--fg);
	}
	.row:hover {
		border-color: var(--accent);
		text-decoration: none;
	}
	.score-pill {
		flex: 0 0 3rem;
		align-self: center;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 2.25rem;
		font-weight: 700;
		padding: 0.4rem 0.5rem;
		border-radius: 6px;
		background: #20262d;
		color: var(--muted);
		font-variant-numeric: tabular-nums;
		line-height: 1;
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
	.status-applied {
		background: rgba(46, 160, 67, 0.2);
		color: var(--ok);
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
</style>
