<script lang="ts">
	import { onMount } from 'svelte';
	import type { UpdateInfo } from '$lib/api';

	let { update }: { update: UpdateInfo | null } = $props();

	// Dismissible per-version (localStorage) so the banner doesn't nag after the
	// user has seen a given release. External link opens in the OS browser (the
	// Electron shell routes non-loopback URLs to shell.openExternal).
	const DISMISS_KEY = 'ja-update-dismissed';
	let dismissed = $state(true); // assume dismissed until localStorage is read
	let show = $derived(!!update?.update_available && !dismissed);

	function dismiss() {
		dismissed = true;
		if (update?.latest) localStorage.setItem(DISMISS_KEY, update.latest);
	}

	onMount(() => {
		const seen = localStorage.getItem(DISMISS_KEY);
		dismissed = !!update?.latest && seen === update.latest;
	});
</script>

{#if show}
	<div class="update-bar" role="status">
		<span class="ub-dot"></span>
		<span class="ub-text">
			Update available — <strong>{update?.latest}</strong>
			<span class="ub-cur">(you have {update?.current})</span>
		</span>
		<a class="ub-link" href={update?.url} target="_blank" rel="noopener">Open Releases ↗</a>
		<button type="button" class="ub-x" aria-label="Dismiss update notice" onclick={dismiss}>✕</button>
	</div>
{/if}

<style>
	.update-bar {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 7px 16px;
		font-size: 12.5px;
		background: var(--accent-soft);
		color: var(--fg);
		border-bottom: 1px solid var(--border);
	}
	.ub-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--accent);
		flex: none;
	}
	.ub-text {
		min-width: 0;
	}
	.ub-cur {
		color: var(--faint);
	}
	.ub-link {
		margin-left: auto;
		color: var(--accent);
		font-weight: 600;
		white-space: nowrap;
	}
	.ub-x {
		color: var(--muted);
		font-size: 12px;
		line-height: 1;
		padding: 4px 6px;
		border-radius: 6px;
		flex: none;
	}
	.ub-x:hover {
		background: var(--border);
		color: var(--fg);
	}
</style>
