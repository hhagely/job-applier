<script lang="ts">
	import type { PageData } from './$types';
	import type { ApplicationStatus } from '$lib/api';

	let { data }: { data: PageData } = $props();

	const statuses: ApplicationStatus[] = [
		'new',
		'interested',
		'drafted',
		'applied',
		'rejected',
		'archived'
	];

	let job = $derived(data.job);
</script>

<a href="/" class="back">← back to queue</a>

<header class="job-header">
	<h1>{job.title}</h1>
	<p class="company">
		{job.company?.name ?? 'Unknown'}
		{#if job.location}· {job.location}{/if}
		{#if job.employment_type}· {job.employment_type}{/if}
	</p>
	<p>
		<a href={job.url} target="_blank" rel="noopener">View original posting →</a>
	</p>
</header>

<section class="grid">
	<div class="panel">
		<h2>Match score</h2>
		{#if job.score}
			<div class="score-big">{job.score.score}<span>/100</span></div>
			{#if job.score.reasoning}
				<p class="reasoning">{job.score.reasoning}</p>
			{/if}
			{#if job.score.rubric && Object.keys(job.score.rubric).length > 0}
				<details>
					<summary>Rubric breakdown</summary>
					<pre>{JSON.stringify(job.score.rubric, null, 2)}</pre>
				</details>
			{/if}
		{:else}
			<p class="muted">
				Not yet scored. Run <code>/match-pending</code> in Claude Code to score the queue.
			</p>
		{/if}
	</div>

	<div class="panel">
		<h2>Status</h2>
		<form method="POST" action="?/setStatus" class="status-form">
			<select name="status" value={job.application?.status ?? 'new'}>
				{#each statuses as s}
					<option value={s}>{s}</option>
				{/each}
			</select>
			<button type="submit">Update</button>
		</form>

		<h3>Notes</h3>
		<form method="POST" action="?/setNotes">
			<textarea name="notes" rows="4" placeholder="Personal notes about this role…"
				>{job.application?.notes ?? ''}</textarea>
			<button type="submit">Save notes</button>
		</form>
	</div>
</section>

<section class="panel description">
	<h2>Description</h2>
	{@html job.description}
</section>

<style>
	.back {
		display: inline-block;
		margin-bottom: 1rem;
		font-size: 0.9rem;
	}
	.job-header h1 {
		margin: 0 0 0.25rem;
		font-size: 1.5rem;
	}
	.company {
		color: var(--muted);
		margin: 0 0 0.5rem;
	}
	.grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
		margin: 1.5rem 0;
	}
	@media (max-width: 720px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}
	.panel {
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
		padding: 1rem 1.25rem;
	}
	.panel h2 {
		font-size: 1rem;
		margin: 0 0 0.75rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.panel h3 {
		font-size: 0.85rem;
		margin: 1rem 0 0.4rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.score-big {
		font-size: 2.5rem;
		font-weight: 700;
	}
	.score-big span {
		font-size: 1rem;
		color: var(--muted);
		font-weight: 400;
	}
	.reasoning {
		color: var(--fg);
		margin-top: 0.5rem;
	}
	.muted {
		color: var(--muted);
	}
	.status-form {
		display: flex;
		gap: 0.5rem;
	}
	select,
	textarea,
	button {
		font: inherit;
		color: var(--fg);
		background: var(--bg);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.4rem 0.6rem;
	}
	textarea {
		width: 100%;
		box-sizing: border-box;
		resize: vertical;
	}
	button {
		cursor: pointer;
		background: var(--panel-border);
	}
	button:hover {
		border-color: var(--accent);
	}
	pre {
		background: var(--bg);
		padding: 0.75rem;
		border-radius: 6px;
		overflow-x: auto;
		font-size: 0.85rem;
	}
	.description :global(p) {
		line-height: 1.5;
	}
	.description :global(ul) {
		padding-left: 1.5rem;
	}
	.description :global(ol) {
		padding-left: 1.5rem;
	}
</style>
