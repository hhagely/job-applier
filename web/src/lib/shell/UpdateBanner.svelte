<script lang="ts">
	import { onMount } from 'svelte';
	import type { UpdateInfo } from '$lib/api';
	import { updater } from '$lib/updater.svelte';

	let { update }: { update: UpdateInfo | null } = $props();

	// --- link banner (plain browser, or Electron auto-update fallback) ---------
	// Dismissible per-version (localStorage) so the banner doesn't nag after the
	// user has seen a given release. External link opens in the OS browser (the
	// Electron shell routes non-loopback URLs to shell.openExternal).
	const DISMISS_KEY = 'ja-update-dismissed';
	let dismissed = $state(true); // assume dismissed until localStorage is read

	function dismiss() {
		dismissed = true;
		if (update?.latest) localStorage.setItem(DISMISS_KEY, update.latest);
	}

	onMount(() => {
		const seen = localStorage.getItem(DISMISS_KEY);
		dismissed = !!update?.latest && seen === update.latest;
	});

	// Inside the desktop shell, electron-updater actually downloads + installs, so
	// it takes over the banner. The server-driven link only shows when the
	// auto-updater isn't driving (a plain browser) or it errored — e.g. a .deb
	// install electron-updater can't self-update, where a manual download is the
	// fallback.
	let ev = $derived(updater.event);
	let autoActive = $derived(updater.available && updater.active);
	let autoErrored = $derived(updater.available && ev.state === 'error');
	let showLink = $derived(
		(!updater.available || autoErrored) && !!update?.update_available && !dismissed
	);
</script>

{#if autoActive}
	<div class="update-bar" role="status">
		<span class="ub-dot"></span>
		{#if ev.state === 'downloaded'}
			<span class="ub-text">
				Update ready{#if ev.version} — <strong>{ev.version}</strong>{/if}
			</span>
			<button type="button" class="ub-action" onclick={() => updater.install()}>
				Restart &amp; install
			</button>
		{:else}
			<span class="ub-text">
				Downloading update{#if ev.version} <strong>{ev.version}</strong>{/if}
				{#if ev.state === 'downloading'}<span class="ub-cur">{ev.percent}%</span>{/if}
			</span>
			<div class="ub-prog" aria-hidden="true">
				<div class="ub-prog-fill" style:width="{ev.percent ?? 0}%"></div>
			</div>
		{/if}
	</div>
{:else if showLink}
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
	.ub-action {
		margin-left: auto;
		color: var(--accent);
		font-weight: 600;
		white-space: nowrap;
		padding: 3px 10px;
		border: 1px solid var(--accent);
		border-radius: 6px;
	}
	.ub-action:hover {
		background: var(--accent);
		color: var(--bg);
	}
	.ub-prog {
		width: 120px;
		height: 5px;
		border-radius: 3px;
		background: var(--border);
		overflow: hidden;
		flex: none;
	}
	.ub-prog-fill {
		height: 100%;
		background: var(--accent);
		transition: width 0.2s ease;
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
