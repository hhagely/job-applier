<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/Icon.svelte';
	import { theme } from '$lib/theme.svelte';
	import { desktop, isElectron } from '$lib/desktop';

	let { onOpenPalette, onOpenHelp }: { onOpenPalette: () => void; onOpenHelp: () => void } =
		$props();

	let mod = $state('Ctrl');
	let electron = $state(false);
	onMount(() => {
		mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
		electron = isElectron();
	});

	const controls = () => desktop()?.windowControls;
</script>

<header class="titlebar">
	<a href="/dashboard" class="brand">
		<span class="logo"><Icon name="logo" size={12} stroke={2.4} /></span>
		<b>job-applier</b><span>· desktop</span>
	</a>

	<button class="tb-search" onclick={onOpenPalette} aria-label="Open command palette">
		<Icon name="search" size={14} stroke={2} />
		Search jobs, companies, commands…
		<span class="k"><kbd>{mod}</kbd><kbd>K</kbd></span>
	</button>

	<div class="tb-actions">
		<button
			class="icon-btn"
			onclick={() => theme.toggle()}
			title="Toggle theme ({mod}+J)"
			aria-label="Toggle light/dark theme"
		>
			<Icon name={theme.effective === 'light' ? 'sun' : 'moon'} size={16} stroke={2} />
		</button>
		<button class="icon-btn" onclick={onOpenHelp} title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">
			<Icon name="help" size={16} stroke={2} />
		</button>

		{#if electron}
			<div style="width:8px"></div>
			<button class="win-ctl" title="Minimize" aria-label="Minimize" onclick={() => controls()?.minimize()}>
				<svg viewBox="0 0 12 12" stroke="currentColor" stroke-width="1.2"><line x1="2" y1="6" x2="10" y2="6" /></svg>
			</button>
			<button class="win-ctl" title="Maximize" aria-label="Maximize" onclick={() => controls()?.maximize()}>
				<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="2.5" y="2.5" width="7" height="7" /></svg>
			</button>
			<button class="win-ctl close" title="Close" aria-label="Close" onclick={() => controls()?.close()}>
				<svg viewBox="0 0 12 12" stroke="currentColor" stroke-width="1.3"><line x1="3" y1="3" x2="9" y2="9" /><line x1="9" y1="3" x2="3" y2="9" /></svg>
			</button>
		{/if}
	</div>
</header>
