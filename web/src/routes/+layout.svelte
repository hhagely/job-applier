<script lang="ts">
	import type { LayoutData } from './$types';

	let { children, data }: { children: import('svelte').Snippet; data: LayoutData } = $props();

	// Serialize the API base for browser-only helpers (getApiBase()). JSON.stringify
	// yields a safe double-quoted string literal for the inline script.
	let apiBaseScript = $derived(`window.__API_BASE__=${JSON.stringify(data.apiBase)};`);
</script>

<svelte:head>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	{@html `<script>${apiBaseScript}</script>`}
</svelte:head>

<div class="app">
	<header>
		<a href="/" class="brand">job-applier</a>
		<nav>
			<a href="/?filter=passed">Queue</a>
			<a href="/?filter=manual">Manual review</a>
			<a href="/followups">Follow-ups</a>
			<a href="/resume">Resume</a>
			<a href="/search">Search profile</a>
		</nav>
	</header>

	<main>
		{@render children()}
	</main>
</div>

<style>
	:global(:root) {
		--bg: #0e1116;
		--panel: #161b22;
		--panel-border: #30363d;
		--fg: #e6edf3;
		--muted: #8b949e;
		--accent: #58a6ff;
		--ok: #2ea043;
		--warn: #d29922;
		--bad: #f85149;
		font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
	}

	:global(html, body) {
		margin: 0;
		padding: 0;
		background: var(--bg);
		color: var(--fg);
	}

	:global(a) {
		color: var(--accent);
		text-decoration: none;
	}
	:global(a:hover) {
		text-decoration: underline;
	}

	.app {
		max-width: 1100px;
		margin: 0 auto;
		padding: 1rem 1.5rem 4rem;
	}

	header {
		display: flex;
		align-items: center;
		gap: 2rem;
		padding: 0.75rem 0 1.5rem;
		border-bottom: 1px solid var(--panel-border);
		margin-bottom: 1.5rem;
	}

	.brand {
		font-weight: 700;
		font-size: 1.1rem;
		color: var(--fg);
	}

	nav {
		display: flex;
		gap: 1.25rem;
		font-size: 0.95rem;
	}

	main {
		min-height: 60vh;
	}
</style>
