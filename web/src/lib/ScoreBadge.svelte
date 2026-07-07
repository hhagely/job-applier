<script lang="ts">
	import { scoreBand } from '$lib/score';

	let {
		score = null,
		size = 'sm',
		stale = false,
		title
	}: {
		score?: number | null;
		size?: 'sm' | 'md' | 'lg';
		stale?: boolean;
		title?: string;
	} = $props();
</script>

<span class="score {size}" data-band={scoreBand(score)} class:stale {title}>
	{score ?? '—'}
</span>

<style>
	.score.stale {
		background: var(--surface-2);
		color: var(--faint);
		outline: 1px dashed var(--border-2);
	}
</style>
