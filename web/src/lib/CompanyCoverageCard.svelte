<script lang="ts">
	// The /search "Companies searched" card: how many job boards a scrape covers,
	// how stale that list is, and the button that refreshes it (?/refreshCompanies
	// on /search, which starts a `refresh_companies` task).
	import { enhance } from '$app/forms';
	import type { CompanyCoverage } from '$lib/api';
	import { DAY_MS, fmtDate } from '$lib/date';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import { createTaskRunner } from '$lib/taskRunner.svelte';

	let { coverage, error = undefined }: { coverage: CompanyCoverage; error?: string } = $props();

	// Progress rides the shared task stream (same as the dashboard's scrape), so
	// it survives navigating away mid-run.
	const companies = createTaskRunner({
		kind: 'refresh_companies',
		failMessage: 'could not start the company update'
	});

	// A month without a check is enough to start missing new employers; never
	// having run at all is treated as stale too.
	const STALE_DAYS = 30;
	const daysSinceCheck = $derived(
		coverage.last_checked_at
			? Math.floor((Date.now() - Date.parse(coverage.last_checked_at)) / DAY_MS)
			: null
	);
	const stale = $derived(daysSinceCheck === null || daysSinceCheck >= STALE_DAYS);
	const lastCheckedLabel = $derived(
		daysSinceCheck === null ? 'never' : daysSinceCheck === 0 ? 'today' : `${daysSinceCheck}d ago`
	);
</script>

<div class="card">
	<div class="card-h"><h2>Companies searched</h2></div>
	<div class="card-b">
		<p class="muted" style="margin-bottom:14px">
			Every scrape pulls from this list of company job boards. Companies open new boards all the
			time, so the list goes out of date — updating it is how new employers enter your queue in the
			first place.
		</p>

		<div class="cov-stats">
			<div class="cov-stat">
				<div class="cov-num">{coverage.total.toLocaleString()}</div>
				<div class="cov-lbl">company job boards</div>
			</div>
			<div class="cov-stat" title={coverage.last_checked_at ? fmtDate(coverage.last_checked_at) : 'never run'}>
				<div class="cov-num" class:stale>{lastCheckedLabel}</div>
				<div class="cov-lbl">last updated</div>
			</div>
		</div>

		{#if stale}
			<p class="banner warn cov-warn">
				This list hasn't been updated in a while. Any company that opened a job board since then is
				invisible to your scrapes.
			</p>
		{/if}

		<ul class="cov-sources">
			{#each Object.entries(coverage.by_source) as [source, n] (source)}
				<li><span class="cov-src">{source}</span><span class="cov-n">{n.toLocaleString()}</span></li>
			{/each}
		</ul>

		<form method="POST" action="?/refreshCompanies" class="cov-actions" use:enhance={companies.enhance}>
			<button type="submit" class="btn primary" disabled={companies.busy}>
				{companies.busy ? 'Updating…' : 'Update company list'}
			</button>
			<label class="cov-reverify">
				<input type="checkbox" name="reverify" disabled={companies.busy} />
				Also remove boards that no longer respond
				<span class="muted">(slower — re-checks all {coverage.total.toLocaleString()})</span>
			</label>
		</form>

		{#if error}
			<p class="err-text" style="margin-top:10px">{error}</p>
		{/if}
		{#if companies.error && !companies.snap}
			<p class="err-text" style="margin-top:10px">{companies.error}</p>
		{/if}
		<div style="margin-top:12px">
			<ScoreProgress
				task={companies.snap}
				onDismiss={companies.dismiss}
				runningVerb="Checking"
				doneVerb="Checked"
				resultsLabel="steps"
			/>
		</div>
	</div>
</div>

<style>
	.cov-stats {
		display: flex;
		gap: 28px;
		margin-bottom: 14px;
	}
	.cov-num {
		font-size: 1.5rem;
		font-weight: 650;
		line-height: 1.1;
	}
	.cov-num.stale {
		color: var(--warn);
	}
	.cov-lbl {
		font-size: 0.78rem;
		color: var(--muted);
		margin-top: 2px;
	}
	.cov-warn {
		margin-bottom: 14px;
	}
	.cov-sources {
		list-style: none;
		margin: 0 0 16px;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.cov-sources li {
		display: flex;
		align-items: baseline;
		gap: 6px;
		padding: 3px 9px;
		border: 1px solid var(--border);
		border-radius: 999px;
		font-size: 0.8rem;
	}
	.cov-src {
		color: var(--muted);
	}
	.cov-n {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
	}
	.cov-actions {
		display: flex;
		align-items: center;
		gap: 14px;
		flex-wrap: wrap;
	}
	.cov-reverify {
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 0.85rem;
		cursor: pointer;
	}
</style>
