<script lang="ts">
	import Icon from '$lib/Icon.svelte';

	let {
		jobs = null,
		unreviewed = null,
		followupsDue = null,
		aiProvider = null,
		onOpenPalette,
		onOpenHelp
	}: {
		jobs?: number | null;
		unreviewed?: number | null;
		followupsDue?: number | null;
		aiProvider?: string | null;
		onOpenPalette: () => void;
		onOpenHelp: () => void;
	} = $props();
</script>

<footer class="statusbar">
	<span class="sb-item"><span class="live"></span> Ingest idle</span>
	{#if jobs != null}<span class="sb-item mono">{jobs} jobs</span>{/if}
	{#if unreviewed != null}<span class="sb-item mono">{unreviewed} unreviewed</span>{/if}
	{#if followupsDue != null && followupsDue > 0}
		<span class="sb-item mono" style="color:var(--weak)">{followupsDue} follow-ups due</span>
	{/if}
	<div class="sb-right">
		<button class="sb-btn sb-item" onclick={onOpenPalette}>
			<Icon name="palette" size={13} stroke={2} /> Command palette
		</button>
		<a class="sb-item" href="/settings" style="color:var(--muted)">
			AI: <b class="mono" style="color:var(--fg)">{aiProvider ?? 'none'}</b>
		</a>
		<button class="sb-btn sb-item" onclick={onOpenHelp}>? Shortcuts</button>
	</div>
</footer>

<style>
	.statusbar a:hover {
		text-decoration: none;
		color: var(--fg);
	}
</style>
