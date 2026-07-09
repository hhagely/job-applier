<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import Titlebar from '$lib/shell/Titlebar.svelte';
	import Sidebar from '$lib/shell/Sidebar.svelte';
	import StatusBar from '$lib/shell/StatusBar.svelte';
	import CommandPalette from '$lib/shell/CommandPalette.svelte';
	import HelpSheet from '$lib/shell/HelpSheet.svelte';
	import { initTheme, theme } from '$lib/theme.svelte';
	import { NAV } from '$lib/shell/nav';
	import type { LayoutData } from './$types';

	let { children, data }: { children: import('svelte').Snippet; data: LayoutData } = $props();

	let apiBaseScript = $derived(`window.__API_BASE__=${JSON.stringify(data.apiBase)};`);

	let paletteOpen = $state(false);
	let helpOpen = $state(false);
	let mod = $state('Ctrl');

	onMount(() => {
		mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
		return initTheme();
	});

	function onKeydown(e: KeyboardEvent) {
		const target = e.target as HTMLElement | null;
		const typing = !!target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
		const metaOrCtrl = e.metaKey || e.ctrlKey;

		if (metaOrCtrl && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			paletteOpen = true;
			return;
		}
		if (metaOrCtrl && e.key >= '1' && e.key <= '6') {
			e.preventDefault();
			const item = NAV[Number(e.key) - 1];
			if (item) goto(item.href);
			return;
		}
		if (metaOrCtrl && e.key.toLowerCase() === 'j') {
			e.preventDefault();
			theme.toggle();
			return;
		}
		if (e.key === 'Escape') {
			paletteOpen = false;
			helpOpen = false;
			return;
		}
		if (typing) return;
		if (e.key === '?') {
			e.preventDefault();
			helpOpen = true;
			return;
		}
		if (e.key === '/') {
			e.preventDefault();
			paletteOpen = true;
			return;
		}
	}
</script>

<svelte:head>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	{@html `<script>${apiBaseScript}</script>`}
</svelte:head>

<svelte:window onkeydown={onKeydown} />

<Titlebar onOpenPalette={() => (paletteOpen = true)} onOpenHelp={() => (helpOpen = true)} />

<div class="shell">
	<Sidebar counts={data.counts ?? {}} />
	<main class="main">
		{@render children()}
	</main>
</div>

<StatusBar
	jobs={data.counts?.jobs ?? null}
	unreviewed={data.counts?.queue ?? null}
	followupsDue={data.counts?.followups ?? null}
	aiProvider={data.aiProvider}
	onOpenPalette={() => (paletteOpen = true)}
	onOpenHelp={() => (helpOpen = true)}
/>

<CommandPalette
	open={paletteOpen}
	onClose={() => (paletteOpen = false)}
	onShowHelp={() => {
		paletteOpen = false;
		helpOpen = true;
	}}
/>

<HelpSheet open={helpOpen} onClose={() => (helpOpen = false)} {mod} />
