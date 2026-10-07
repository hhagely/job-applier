<script lang="ts">
	// The /search "Check a company" card: is an employer already searched, and if
	// not, add their board. Posts to /search's ?/addCompany and ?/removeCompany;
	// the page passes the result back in.
	import { enhance } from '$app/forms';
	import type { WatchedCompany } from '$lib/api';
	import { whileBusy } from '$lib/busy';
	import RemovableList from '$lib/RemovableList.svelte';

	let {
		watched,
		error = undefined,
		message = undefined,
		already = false
	}: {
		watched: WatchedCompany[];
		error?: string;
		message?: string;
		/** The company was already searched: show the message as a notice, not a success. */
		already?: boolean;
	} = $props();

	// The add POST probes live ATS APIs, so it's seconds-slow: hold the form
	// disabled rather than letting it look like nothing happened.
	let adding = $state(false);
</script>

<div class="card">
	<div class="card-h"><h2>Check a company</h2></div>
	<div class="card-b">
		<p class="muted" style="margin-bottom:14px">
			Have an employer in mind? Type their name to find out whether your scrapes already cover
			them — most well-known companies are on the list already. If they aren't, and their job
			board is one we can read (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday, or
			Jibe), it gets added and searched from the next scrape on. Pasting the URL of their job board
			works too, and is more exact than a name.
		</p>

		<form method="POST" action="?/addCompany" class="wl-add" use:enhance={whileBusy((b) => (adding = b))}>
			<input
				class="input"
				type="text"
				name="query"
				placeholder="Company name or job-board URL"
				autocomplete="off"
				disabled={adding}
				required
			/>
			<button type="submit" class="btn primary" disabled={adding}>
				{adding ? 'Checking…' : 'Check'}
			</button>
		</form>
		<small class="muted wl-hint">Takes a few seconds — we ask each job board directly.</small>

		{#if error}
			<p class="err-text" style="margin-top:10px">{error}</p>
		{/if}
		{#if message}
			<p class="banner {already ? 'warn' : 'ok'}" style="margin-top:10px">{message}</p>
		{/if}

		{#if watched.length > 0}
			<div class="wl-list-head">Added this way</div>
			<RemovableList items={watched} action="?/removeCompany" label={(c) => c.label}>
				{#snippet row(c)}
					<span class="rl-name">{c.label}</span>
					<span class="rl-sub">
						{c.source} / {c.slug}
						{#if c.last_job_count !== null && c.last_job_count !== undefined}
							· {c.last_job_count} roles at last check
						{/if}
						{#if !c.enabled}· <span class="wl-off">disabled — board stopped responding</span>{/if}
					</span>
				{/snippet}
			</RemovableList>
		{/if}
	</div>
</div>

<style>
	.wl-add {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.wl-add .input {
		flex: 1;
		min-width: 220px;
	}
	.wl-hint {
		display: block;
		margin-top: 6px;
	}
	.wl-list-head {
		margin-top: 18px;
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
	}
	.wl-off {
		color: var(--warn);
	}
</style>
