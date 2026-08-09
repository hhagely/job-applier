<script lang="ts">
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Icon from '$lib/Icon.svelte';
	import ScoreBadge from '$lib/ScoreBadge.svelte';
	import { api, getApiBase, type Job } from '$lib/api';
	import { isArchived } from '$lib/jobFilters';
	import { theme } from '$lib/theme.svelte';
	import { NAV } from './nav';
	import { emitCommand, type ShellCommand } from './commandBus';
	import { updater } from '$lib/updater.svelte';

	let {
		open = false,
		onClose,
		onShowHelp
	}: { open?: boolean; onClose: () => void; onShowHelp: () => void } = $props();

	interface Cmd {
		/** Unique within a render — job titles collide across companies, so rows
		 *  carry an explicit key rather than being keyed on their label. */
		key: string;
		t: string;
		cat: string;
		ico: string;
		/** Set on posting results; drives the score badge + archived hint. */
		job?: Job;
		run: () => void | Promise<void>;
	}

	async function runDashboardCommand(name: ShellCommand) {
		if (page.url.pathname !== '/dashboard') await goto('/dashboard');
		emitCommand(name);
	}

	const commands: Cmd[] = [
		...NAV.map((n) => ({
			key: `nav:${n.href}`,
			t: `Go to ${n.label}`,
			cat: 'Navigate',
			ico: n.icon,
			run: () => goto(n.href)
		})),
		{ key: 'scrape', t: 'Run scrape now', cat: 'Action', ico: 'refresh', run: () => runDashboardCommand('scrape') },
		{ key: 'score', t: 'Score pending jobs', cat: 'Action', ico: 'star', run: () => runDashboardCommand('score') },
		{ key: 'theme', t: 'Toggle light / dark theme', cat: 'Action', ico: 'sun', run: () => theme.toggle() },
		// Only in the desktop shell, where electron-updater can actually check.
		...(updater.present
			? [{ key: 'update', t: 'Check for updates', cat: 'Action', ico: 'download', run: () => updater.check() }]
			: []),
		{ key: 'help', t: 'Show keyboard shortcuts', cat: 'Help', ico: 'key', run: () => onShowHelp() }
	];

	let query = $state('');
	let idx = $state(0);
	let inputEl = $state<HTMLInputElement | null>(null);

	const matchingCommands = $derived(
		query.trim() === ''
			? commands
			: commands.filter((c) => (c.t + ' ' + c.cat).toLowerCase().includes(query.toLowerCase()))
	);

	// --- ingested-posting search ------------------------------------------------
	// The commands above are static and filter locally; job/company matches come
	// from the API, so they're debounced and guarded by a sequence number (a slow
	// response for "eng" must not overwrite the results for "engineer"). Aborting
	// instead would fight api.ts's GET retry, which treats an abort as a failure.
	const SEARCH_MIN_TERM = 2; // mirrors services.SEARCH_MIN_TERM
	const SEARCH_DEBOUNCE_MS = 160;

	let jobs = $state<Job[]>([]);
	let searching = $state(false);
	let seq = 0;
	let debounce: ReturnType<typeof setTimeout> | null = null;

	function resetSearch() {
		if (debounce) clearTimeout(debounce);
		debounce = null;
		seq++; // invalidate any in-flight response
		jobs = [];
		searching = false;
	}

	function scheduleSearch(term: string) {
		if (debounce) clearTimeout(debounce);
		if (term.length < SEARCH_MIN_TERM) {
			seq++;
			jobs = [];
			searching = false;
			return;
		}
		searching = true;
		debounce = setTimeout(async () => {
			const mine = ++seq;
			try {
				const found = await api.searchJobs(fetch, getApiBase(), term);
				if (mine === seq) jobs = found;
			} catch {
				if (mine === seq) jobs = []; // offline / API down — commands still work
			} finally {
				if (mine === seq) searching = false;
			}
		}, SEARCH_DEBOUNCE_MS);
	}

	$effect(() => {
		const term = query.trim();
		if (!open) return;
		// Every keystroke re-aims the highlight at the top hit; without this a
		// selection made against the old result set silently lands on a different row.
		idx = 0;
		scheduleSearch(term);
	});

	// Postings first when the user is clearly searching for one — commands stay
	// reachable below, and an empty query shows the plain command list.
	const jobItems = $derived(
		jobs.map(
			(j): Cmd => ({
				key: `job:${j.id}`,
				t: j.title,
				cat: j.company?.name ?? 'Unknown company',
				ico: 'briefcase',
				job: j,
				run: () => goto(`/jobs/${j.id}`)
			})
		)
	);
	const filtered = $derived([...jobItems, ...matchingCommands]);

	$effect(() => {
		if (open) {
			query = '';
			idx = 0;
			tick().then(() => inputEl?.focus());
		} else {
			resetSearch();
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
					placeholder="Search a job or company, or run a command…"
					autocomplete="off"
					aria-label="Command palette input"
				/>
				<kbd>Esc</kbd>
			</div>
			<div class="palette-list">
				{#each filtered as c, i (c.key)}
					<button
						type="button"
						class="pcmd"
						class:active={i === idx}
						onmouseenter={() => (idx = i)}
						onclick={() => runAt(i)}
					>
						<span class="pc-ico"><Icon name={c.ico} size={16} stroke={2} /></span>
						<span class="pc-t">{c.t}</span>
						{#if c.job}
							{#if isArchived(c.job)}<span class="pc-flag">archived</span>{/if}
							<span class="pc-cat">{c.cat}</span>
							<ScoreBadge score={c.job.score?.score ?? null} stale={c.job.score?.is_stale ?? false} />
						{:else}
							<span class="pc-cat">{c.cat}</span>
						{/if}
					</button>
				{:else}
					<div class="palette-empty">
						{#if searching}
							Searching…
						{:else if query.trim().length >= SEARCH_MIN_TERM}
							No jobs, companies, or commands match “{query.trim()}”
						{:else}
							No commands
						{/if}
					</div>
				{/each}
				{#if searching && filtered.length > 0}
					<div class="palette-note">Searching postings…</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
