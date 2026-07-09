<script lang="ts">
	// Shared match-score display: the number hero, when/against-what it was scored,
	// the rationale, and the per-bucket rubric bars. Used by both the Queue detail
	// pane and /jobs/[id] so the two can't drift. Renders nothing when unscored —
	// the parent owns the "not scored yet" empty state and the surrounding card.
	import Icon from '$lib/Icon.svelte';
	import { scoreBandVar } from '$lib/score';
	import { fmtDateTime } from '$lib/date';
	import type { Score } from '$lib/api';

	let { score }: { score: Score | null | undefined } = $props();

	function rubricEntries(rubric: Record<string, unknown> | undefined): [string, unknown][] {
		return rubric ? Object.entries(rubric) : [];
	}
	function rubricNumber(value: unknown): number | null {
		return typeof value === 'number' && value >= 0 && value <= 100 ? value : null;
	}
</script>

{#if score}
	{#if score.is_stale}
		<p class="banner warn" style="margin-bottom:12px">
			Scored against an older resume — re-score to refresh.
		</p>
	{/if}
	<div class="match-hero">
		<span class="mh-n" style="color:{scoreBandVar(score.score)}">{score.score}</span>
		<span class="mh-d">/100</span>
	</div>
	<p class="score-meta">
		<span>{fmtDateTime(score.scored_at)}</span>
		{#if score.resume_filename}· <span class="mono">{score.resume_filename}</span>{/if}
	</p>
	{#if score.reasoning}<div class="rationale">{score.reasoning}</div>{/if}
	{#if rubricEntries(score.rubric).length > 0}
		<details class="rubric">
			<summary><Icon name="chevron" size={12} stroke={2.4} /> Rubric breakdown</summary>
			{#each rubricEntries(score.rubric) as [label, value] (label)}
				{@const num = rubricNumber(value)}
				<div class="rub-row">
					<div class="rr-l">{label}</div>
					{#if num !== null}
						<div class="rr-track">
							<div class="rr-fill" style="width:{num}%;background:{scoreBandVar(num)}"></div>
						</div>
						<div class="rr-n">{num}</div>
					{:else}
						<div class="rr-v">
							{typeof value === 'object' ? JSON.stringify(value) : String(value)}
						</div>
					{/if}
				</div>
			{/each}
		</details>
	{/if}
{/if}

<style>
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
	.score-meta {
		color: var(--faint);
		font-size: 12px;
		margin: 8px 0 0;
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
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
</style>
