<script lang="ts">
	import type { TaskSnapshot } from '$lib/api';

	let {
		task,
		onDismiss,
		runningVerb = 'Scoring',
		doneVerb = 'Scored',
		resultsLabel = 'per-job results'
	}: {
		task: TaskSnapshot | null | undefined;
		onDismiss: () => void;
		runningVerb?: string;
		doneVerb?: string;
		resultsLabel?: string;
	} = $props();

	let resultsOpen = $state(false);
	const pct = $derived(task && task.total > 0 ? Math.round((task.done / task.total) * 100) : 100);
</script>

{#if task}
<section class="score-panel" class:done={task.status !== 'running'}>
	<div class="score-panel-head">
		<strong>
			{#if task.status === 'running'}
				{runningVerb} {task.done}/{task.total}…
			{:else if task.status === 'error'}
				{runningVerb} failed
			{:else}
				{doneVerb} {task.done}/{task.total}
			{/if}
		</strong>
		{#if task.errors.length > 0}
			<span class="score-errcount"
				>{task.errors.length} error{task.errors.length === 1 ? '' : 's'}</span
			>
		{/if}
		{#if task.status !== 'running'}
			<button type="button" class="score-dismiss" onclick={onDismiss}>Dismiss</button>
		{/if}
	</div>

	<div class="meter score-progress"><i class="score-bar" style={`width:${pct}%`}></i></div>

	{#if task.results.length > 0}
		<button type="button" class="score-toggle" onclick={() => (resultsOpen = !resultsOpen)}>
			{resultsOpen ? 'Hide' : 'Show'} {resultsLabel}
		</button>
		{#if resultsOpen}
			<ul class="score-results">
				{#each task.results as line, i (i)}
					<li>{line}</li>
				{/each}
			</ul>
		{/if}
	{/if}

	{#if task.errors.length > 0}
		<ul class="score-errlist">
			{#each task.errors as err, i (i)}
				<li>{err}</li>
			{/each}
		</ul>
	{/if}
</section>
{/if}

<style>
	.score-panel {
		background: var(--surface);
		border: 1px solid var(--accent);
		border-radius: 8px;
		padding: 0.75rem 0.9rem;
		margin-bottom: 1rem;
	}
	.score-panel.done {
		border-color: var(--border);
	}
	.score-panel-head {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-bottom: 0.5rem;
		font-size: 0.9rem;
	}
	.score-errcount {
		color: var(--weak);
		font-size: 0.8rem;
	}
	.score-dismiss {
		margin-left: auto;
		background: transparent;
		color: var(--muted);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.2rem 0.55rem;
		cursor: pointer;
		font-size: 0.8rem;
	}
	.score-dismiss:hover {
		color: var(--fg);
		border-color: var(--accent);
	}
	.score-progress {
		--meter-h: 6px;
	}
	.score-bar {
		background: var(--accent);
		transition: width 0.3s ease;
	}
	.score-toggle {
		margin-top: 0.5rem;
		background: transparent;
		color: var(--accent);
		border: 0;
		padding: 0;
		cursor: pointer;
		font-size: 0.8rem;
	}
	.score-results,
	.score-errlist {
		list-style: none;
		padding: 0;
		margin: 0.4rem 0 0;
		font-family: var(--mono);
		font-size: 0.78rem;
		max-height: 14rem;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.score-results li {
		color: var(--muted);
	}
	.score-errlist li {
		color: var(--weak);
	}
</style>
