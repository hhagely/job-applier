<script lang="ts">
	// Shared match-score display: the number hero, when/against-what it was scored,
	// the rationale, and the per-bucket rubric bars. Used by both the Queue detail
	// pane and /jobs/[id] so the two can't drift. Renders nothing when unscored —
	// the parent owns the "not scored yet" empty state and the surrounding card.
	import Icon from '$lib/Icon.svelte';
	import { rubricPercent, rubricWeight, scoreBandVar } from '$lib/score';
	import { fmtDateTime } from '$lib/date';
	import type { Score } from '$lib/api';

	let { score }: { score: Score | null | undefined } = $props();

	type RubricBucket = { points: number; note: string | null };

	let entries: [string, unknown][] = $derived(score?.rubric ? Object.entries(score.rubric) : []);

	/**
	 * The scoring prompts (ai/prompts/score.md + score_batch.md) emit each bucket as
	 * `{"points": 26, "note": "..."}`, but rows scored before that shape — and older
	 * MatchScoreHistory reads — hold a bare number, so both must render. Anything else
	 * returns null and the caller shows the raw value, so a future prompt change
	 * degrades visibly instead of silently drawing a wrong bar.
	 */
	function rubricBucket(value: unknown): RubricBucket | null {
		if (typeof value === 'number') {
			return inRange(value) ? { points: value, note: null } : null;
		}
		if (typeof value === 'object' && value !== null) {
			const { points, note } = value as { points?: unknown; note?: unknown };
			if (typeof points === 'number' && inRange(points)) {
				const text = typeof note === 'string' ? note.trim() : '';
				return { points, note: text || null };
			}
		}
		return null;
	}
	function inRange(n: number): boolean {
		return Number.isFinite(n) && n >= 0 && n <= 100;
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
	{#if entries.length > 0}
		<details class="rubric">
			<summary><Icon name="chevron" size={12} stroke={2.4} /> Rubric breakdown</summary>
			{#each entries as [label, value] (label)}
				{@const bucket = rubricBucket(value)}
				<div class="rub-row">
					<div class="rr-l">{label}</div>
					{#if bucket}
						{@const max = rubricWeight(label)}
						{@const pct = rubricPercent(bucket.points, max)}
						<!-- Fill and band are the bucket's share of ITS weight, not of 100:
						     hard_requirements maxes out at 10, so raw points would draw a
						     perfect score as a 10% rose sliver. -->
						<div class="meter" title="{bucket.points} of {max} ({pct}%)">
							<i style="width:{pct}%;background:{scoreBandVar(pct)}"></i>
						</div>
						<div class="rr-n">{bucket.points}<span class="rr-max">/{max}</span></div>
						{#if bucket.note}<div class="rr-note">{bucket.note}</div>{/if}
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
		grid-template-columns: 150px 1fr 52px;
		gap: 10px;
		align-items: center;
		margin-top: 11px;
	}
	.rub-row .rr-l {
		font-size: 12px;
		color: var(--muted);
	}
	.rub-row .rr-n {
		font-family: var(--mono);
		font-size: 12px;
		text-align: right;
		color: var(--muted);
	}
	/* the bucket's weight, so "10" reads as full marks rather than a low number */
	.rub-row .rr-n .rr-max {
		color: var(--faint);
		font-size: 11px;
	}
	.rub-row .rr-v {
		grid-column: 2 / 4;
		font-family: var(--mono);
		font-size: 11.5px;
		color: var(--muted);
	}
	/* the model's one-line justification for the bucket, under its bar */
	.rub-row .rr-note {
		grid-column: 2 / 4;
		font-size: 11.5px;
		line-height: 1.35;
		color: var(--faint);
		margin-top: 2px;
	}
</style>
