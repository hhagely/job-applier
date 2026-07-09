<script lang="ts">
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import Icon from '$lib/Icon.svelte';
	import { theme } from '$lib/theme.svelte';
	import { NAV } from './nav';
	import { emitCommand, type ShellCommand } from './commandBus';

	let {
		open = false,
		onClose,
		onShowHelp
	}: { open?: boolean; onClose: () => void; onShowHelp: () => void } = $props();

	interface Cmd {
		t: string;
		cat: string;
		ico: string;
		run: () => void | Promise<void>;
	}

	async function runDashboardCommand(name: ShellCommand) {
		if ($page.url.pathname !== '/dashboard') await goto('/dashboard');
		emitCommand(name);
	}

	const commands: Cmd[] = [
		...NAV.map((n) => ({
			t: `Go to ${n.label}`,
			cat: 'Navigate',
			ico: n.icon,
			run: () => goto(n.href)
		})),
		{ t: 'Run scrape now', cat: 'Action', ico: 'refresh', run: () => runDashboardCommand('scrape') },
		{ t: 'Score pending jobs', cat: 'Action', ico: 'star', run: () => runDashboardCommand('score') },
		{ t: 'Toggle light / dark theme', cat: 'Action', ico: 'sun', run: () => theme.toggle() },
		{ t: 'Show keyboard shortcuts', cat: 'Help', ico: 'key', run: () => onShowHelp() }
	];

	let query = $state('');
	let idx = $state(0);
	let inputEl = $state<HTMLInputElement | null>(null);

	const filtered = $derived(
		query.trim() === ''
			? commands
			: commands.filter((c) => (c.t + ' ' + c.cat).toLowerCase().includes(query.toLowerCase()))
	);

	$effect(() => {
		if (open) {
			query = '';
			idx = 0;
			tick().then(() => inputEl?.focus());
		}
	});

	// keep idx in range as the filter narrows
	$effect(() => {
		if (idx > filtered.length - 1) idx = Math.max(0, filtered.length - 1);
	});

	async function runAt(i: number) {
		const c = filtered[i];
		if (!c) return;
		onClose();
		await c.run();
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			idx = Math.min(idx + 1, filtered.length - 1);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			idx = Math.max(idx - 1, 0);
		} else if (e.key === 'Enter') {
			e.preventDefault();
			runAt(idx);
		} else if (e.key === 'Escape') {
			e.preventDefault();
			onClose();
		}
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div class="scrim" onclick={(e) => e.target === e.currentTarget && onClose()}>
		<div class="palette" role="dialog" aria-label="Command palette" aria-modal="true">
			<div class="palette-in">
				<Icon name="search" size={17} stroke={2} />
				<!-- svelte-ignore a11y_autofocus -->
				<input
					bind:this={inputEl}
					bind:value={query}
					onkeydown={onKeydown}
					placeholder="Jump to a view or run a command…"
					autocomplete="off"
					aria-label="Command palette input"
				/>
				<kbd>Esc</kbd>
			</div>
			<div class="palette-list">
				{#each filtered as c, i (c.t)}
					<button
						type="button"
						class="pcmd"
						class:active={i === idx}
						onmouseenter={() => (idx = i)}
						onclick={() => runAt(i)}
					>
						<span class="pc-ico"><Icon name={c.ico} size={16} stroke={2} /></span>
						<span class="pc-t">{c.t}</span>
						<span class="pc-cat">{c.cat}</span>
					</button>
				{:else}
					<div class="palette-empty">No commands</div>
				{/each}
			</div>
		</div>
	</div>
{/if}
