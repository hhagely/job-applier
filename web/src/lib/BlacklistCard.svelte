<script lang="ts">
	// The /search "Company blacklist" card. Its forms post to /search's
	// ?/addBlacklist and ?/removeBlacklist; the page passes the result back in.
	import { enhance } from '$app/forms';
	import type { BlacklistedCompany } from '$lib/api';
	import RemovableList from '$lib/RemovableList.svelte';

	let {
		entries,
		error = undefined,
		message = undefined
	}: { entries: BlacklistedCompany[]; error?: string; message?: string } = $props();
</script>

<div class="card">
	<div class="card-h"><h2>Company blacklist</h2></div>
	<div class="card-b">
		<p class="muted" style="margin-bottom:14px">
			Jobs from these companies never reach this profile's queue (other profiles keep their own
			list). Matching ignores casing, punctuation, and legal suffixes, so <em>Meta</em>,
			<em>Meta Inc</em>, and <em>Meta, Inc.</em> all count as the same company. Adding or removing
			a company re-checks the jobs already found, so there's no need to scrape again.
		</p>

		<form method="POST" action="?/addBlacklist" class="bl-add" use:enhance>
			<input class="input" type="text" name="company" placeholder="Company name" autocomplete="off" required />
			<input class="input" type="text" name="reason" placeholder="Reason (optional)" autocomplete="off" />
			<button type="submit" class="btn primary">Add</button>
		</form>

		{#if error}
			<p class="err-text" style="margin-top:10px">{error}</p>
		{/if}
		{#if message}
			<p class="banner ok" style="margin-top:10px">{message}</p>
		{/if}

		{#if entries.length === 0}
			<p class="muted bl-empty">No companies blacklisted yet.</p>
		{:else}
			<RemovableList items={entries} action="?/removeBlacklist" label={(c) => c.name}>
				{#snippet row(c)}
					<span class="rl-name">{c.name}</span>
					{#if c.reason}<span class="rl-sub">{c.reason}</span>{/if}
				{/snippet}
			</RemovableList>
		{/if}
	</div>
</div>

<style>
	.bl-add {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.bl-add .input {
		flex: 1;
		min-width: 140px;
	}
	.bl-empty {
		margin-top: 14px;
	}
</style>
