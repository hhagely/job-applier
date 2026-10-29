<script lang="ts">
	// The /search "Where you can work" field: the US is always searched, and the
	// toggle unlocks adding other countries, each with its own visa and hybrid
	// settings. It lives inside the criteria form and posts its rows as one JSON
	// field (`markets`), so Save criteria saves it with everything else. Mount it
	// inside a {#key} on the profile so a save or a profile switch re-seeds it.
	import { untrack } from 'svelte';
	import type { Country, Market } from '$lib/api';

	let { markets, countries }: { markets: Market[]; countries: Country[] } = $props();

	type Row = { country: string; needs_sponsorship: boolean; cities: string };

	let enabled = $state(untrack(() => markets.length > 0));
	let rows = $state<Row[]>(
		untrack(() =>
			markets.map((m) => ({
				country: m.country,
				needs_sponsorship: m.needs_sponsorship,
				cities: m.hybrid_cities.join(', ')
			}))
		)
	);
	let query = $state('');
	let addError = $state('');

	const byCode = $derived(new Map(countries.map((c) => [c.code, c])));
	const available = $derived(countries.filter((c) => !rows.some((r) => r.country === c.code)));

	// What the save posts. Toggled off sends [] (the US only); the rows stay in
	// the editor so flipping it back on before saving loses nothing.
	const payload = $derived(
		JSON.stringify(
			enabled
				? rows.map((r) => ({
						country: r.country,
						needs_sponsorship: r.needs_sponsorship,
						hybrid_cities: r.cities
							.split(',')
							.map((c) => c.trim())
							.filter(Boolean)
					}))
				: []
		)
	);

	function resolve(text: string): Country | undefined {
		const key = text.trim().toLowerCase();
		if (!key) return undefined;
		return countries.find(
			(c) =>
				c.name.toLowerCase() === key ||
				c.code.toLowerCase() === key ||
				c.aliases.some((a) => a.toLowerCase() === key)
		);
	}

	function add() {
		const match = resolve(query);
		if (!match) {
			addError = query.trim()
				? `"${query.trim()}" isn't a country we can search. Pick one from the list.`
				: 'Type a country to add.';
			return;
		}
		if (!rows.some((r) => r.country === match.code)) {
			rows.push({ country: match.code, needs_sponsorship: false, cities: '' });
		}
		query = '';
		addError = '';
	}

	function onKeydown(e: KeyboardEvent) {
		// Enter adds the country instead of submitting the whole criteria form.
		if (e.key === 'Enter') {
			e.preventDefault();
			add();
		}
	}
</script>

<div class="field markets">
	<span>Where you can work</span>
	<input type="hidden" name="markets" value={payload} />

	<div class="mk-toggle">
		<button
			type="button"
			class="switch"
			role="switch"
			aria-checked={enabled}
			aria-label="Search outside the United States"
			onclick={() => (enabled = !enabled)}
		></button>
		<span class="mk-toggle-label">Search outside the United States</span>
	</div>
	<small>
		The United States is always searched, remote roles only. Turn this on to add other countries.
		Jobs in a newly added country arrive with the next scrape.
	</small>

	{#if enabled}
		<div class="mk-add">
			<input
				class="input"
				type="text"
				list="market-countries"
				placeholder="Add a country, e.g. United Kingdom"
				aria-label="Add a country"
				autocomplete="off"
				bind:value={query}
				onkeydown={onKeydown}
			/>
			<datalist id="market-countries">
				{#each available as c (c.code)}
					<option value={c.name}></option>
				{/each}
			</datalist>
			<button type="button" class="btn" aria-label="Add country" onclick={add}>Add</button>
		</div>
		{#if addError}<p class="err-text">{addError}</p>{/if}

		{#if rows.length === 0}
			<p class="muted mk-empty">No other countries yet.</p>
		{/if}
		{#each rows as row, i (row.country)}
			<div class="mk-row">
				<div class="mk-row-h">
					<strong>{byCode.get(row.country)?.name ?? row.country}</strong>
					<button
						type="button"
						class="btn ghost sm"
						aria-label={`Remove ${byCode.get(row.country)?.name ?? row.country}`}
						onclick={() => rows.splice(i, 1)}>Remove</button
					>
				</div>
				<label class="mk-check">
					<input type="checkbox" bind:checked={row.needs_sponsorship} />
					I'd need visa sponsorship to work here
				</label>
				<small class="mk-hint">
					Drops postings that say they can't sponsor or need an existing right to work.
				</small>
				<label class="mk-cities">
					<span>Hybrid or on-site OK in</span>
					<input
						class="input"
						type="text"
						placeholder="e.g. Edinburgh, Glasgow"
						autocomplete="off"
						bind:value={row.cities}
					/>
				</label>
				<small class="mk-hint">Comma-separated cities. Leave empty for remote roles only.</small>
			</div>
		{/each}
	{/if}
</div>

<style>
	.mk-toggle {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.mk-toggle-label {
		font-weight: 500;
	}
	.mk-add {
		display: flex;
		gap: 8px;
		margin-top: 10px;
		max-width: 480px;
	}
	.mk-add .input {
		flex: 1;
		min-width: 0;
	}
	.mk-empty {
		margin-top: 8px;
	}
	.mk-row {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-top: 10px;
		padding: 12px;
		border: 1px solid var(--border);
		border-radius: 8px;
		max-width: 480px;
	}
	.mk-row-h {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	.mk-check {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.mk-cities {
		display: flex;
		flex-direction: column;
		gap: 4px;
		margin-top: 4px;
	}
	.mk-hint {
		color: var(--faint);
	}
</style>
