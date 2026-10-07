<script lang="ts">
	import { enhance } from '$app/forms';
	import { whileBusy } from '$lib/busy';
	import { untrack } from 'svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import BlacklistCard from '$lib/BlacklistCard.svelte';
	import CompanyCoverageCard from '$lib/CompanyCoverageCard.svelte';
	import ProfilesCard from '$lib/ProfilesCard.svelte';
	import WatchedCompaniesCard from '$lib/WatchedCompaniesCard.svelte';
	import { US_STATES } from '$lib/usStates';
	import { createTaskRunner } from '$lib/taskRunner.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let profile = $derived(form?.profile ?? data.profile);
	let saving = $state(false);

	let role_titles = $state(untrack(() => joinList(data.profile.role_titles)));
	let title_terms = $state(untrack(() => joinList(data.profile.title_terms ?? [])));
	let seniority_terms = $state(untrack(() => joinList(data.profile.seniority_terms)));
	let required_tech = $state(untrack(() => joinList(data.profile.required_tech)));
	let excluded_tech = $state(untrack(() => joinList(data.profile.excluded_tech)));
	let extracted_skills = $state(untrack(() => joinList(data.profile.extracted_skills)));
	let home_state = $state(untrack(() => data.profile.home_state ?? ''));

	// Re-seed the textareas when the profile changes underneath them: a save or an
	// accepted draft bumps updated_at, and switching profiles changes the id.
	let lastSeen = $state(untrack(() => `${data.profile.id}@${data.profile.updated_at}`));
	$effect(() => {
		const key = `${profile.id}@${profile.updated_at}`;
		if (profile.updated_at && key !== lastSeen) {
			role_titles = joinList(profile.role_titles);
			title_terms = joinList(profile.title_terms ?? []);
			seniority_terms = joinList(profile.seniority_terms);
			required_tech = joinList(profile.required_tech);
			excluded_tech = joinList(profile.excluded_tech);
			extracted_skills = joinList(profile.extracted_skills);
			home_state = profile.home_state ?? '';
			lastSeen = key;
		}
	});

	// --- Profiles -------------------------------------------------------------
	// Saving criteria, adding a profile, or changing the blacklist re-matches the
	// profile against every stored posting in the background (no scrape). Its
	// progress rides the shared task stream as kind `match`, ref = profile id.
	const rematch = createTaskRunner({ kind: 'match', ref: () => String(profile.id ?? '') });

	function joinList(items: string[]): string {
		return items.join('\n');
	}

	let draft = $derived(profile.recommendations_draft);
	const hasProvider = $derived(Boolean(data.aiProvider));
	let suggesting = $state(false);
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>Search profiles</h1>
		<div class="vh-sub">What the ingest filter keeps. One entry per line — commas also work.</div>
	</div>
	<div class="vh-actions">
		<button type="submit" form="save-form" class="btn primary" disabled={saving}>{saving ? 'Saving…' : 'Save criteria'}</button>
	</div>
</div>

<div class="view-body">
	<div class="stack">
		<ProfilesCard
			profiles={data.profiles}
			error={form && 'profileError' in form ? form.profileError : undefined}
			message={form && 'profileMessage' in form ? form.profileMessage : undefined}
		/>

		{#if rematch.snap}
			<ScoreProgress
				task={rematch.snap}
				onDismiss={rematch.dismiss}
				runningVerb="Matching"
				doneVerb="Matched"
				resultsLabel="jobs"
			/>
		{/if}

		{#if profile.using_defaults}
			<p class="banner info">
				This profile's criteria are empty — the filter is using built-in defaults.
				{#if data.hasResume}
					Use <em>Suggest roles from resume</em> in the Criteria card for recommendations.
				{:else}
					Upload a resume first, then suggest roles for recommendations.
				{/if}
			</p>
		{/if}
		{#if hasProvider && !data.hasResume}
			<p class="muted">Upload a resume first to enable suggestions.</p>
		{/if}
		{#if form?.message}<p class="banner ok">{form.message}</p>{/if}
		{#if form?.error}<p class="err-text">{form.error}</p>{/if}

		{#if draft}
			<div class="card" style="border-color:var(--accent)">
				<div class="card-h"><h2>Recommendations</h2></div>
				<div class="card-b">
					{#if draft.rationale}<p class="muted" style="margin-bottom:12px">{draft.rationale}</p>{/if}
					<div class="meta-table">
						<div class="d-meta-row"><span class="dm-k">Role titles</span><span class="dm-v">{draft.role_titles.join(', ') || '—'}</span></div>
						<div class="d-meta-row"><span class="dm-k">Title keywords</span><span class="dm-v">{(draft.title_terms ?? []).join(', ') || '—'}</span></div>
						<div class="d-meta-row"><span class="dm-k">Seniority</span><span class="dm-v">{draft.seniority_terms.join(', ') || '—'}</span></div>
						<div class="d-meta-row"><span class="dm-k">Required tech</span><span class="dm-v">{draft.required_tech.join(', ') || '—'}</span></div>
						<div class="d-meta-row"><span class="dm-k">Excluded tech</span><span class="dm-v">{draft.excluded_tech.join(', ') || '—'}</span></div>
						<div class="d-meta-row"><span class="dm-k">Skills detected</span><span class="dm-v">{draft.extracted_skills.join(', ') || '—'}</span></div>
					</div>
					<div class="rec-actions">
						<form method="POST" action="?/acceptDraft" use:enhance>
							<input type="hidden" name="mode" value="replace" />
							<button type="submit" class="btn primary">Replace with these</button>
						</form>
						<form method="POST" action="?/acceptDraft" use:enhance>
							<input type="hidden" name="mode" value="append" />
							<button type="submit" class="btn">Add to current</button>
						</form>
						<form method="POST" action="?/rejectDraft" use:enhance>
							<button type="submit" class="btn ghost">Dismiss</button>
						</form>
					</div>
				</div>
			</div>
		{/if}

		<div class="card">
			<div class="card-h">
				<h2>Criteria · {profile.name}</h2>
				<div class="criteria-actions">
					{#if !hasProvider}
						<a class="btn danger sm" href="/settings" title="Select an AI CLI in Settings">Suggest roles — set up AI</a>
					{:else}
						<form
							method="POST"
							action="?/suggest"
							use:enhance={whileBusy((b) => (suggesting = b))}
						>
							<button
								type="submit"
								class="btn sm"
								disabled={suggesting || !data.hasResume}
								title={data.hasResume ? undefined : 'Upload a resume first'}
							>
								{suggesting ? 'Analyzing resume…' : 'Suggest roles from resume'}
							</button>
						</form>
					{/if}
				</div>
			</div>
			<form
				id="save-form"
				method="POST"
				action="?/save"
				use:enhance={whileBusy((b) => (saving = b))}
			>
				<div class="card-b">
					<div class="field state-field">
						<span>State of residence <span style="color:var(--faint);font-weight:500">(optional)</span></span>
						<select class="input state-select" name="home_state" aria-label="State of residence" bind:value={home_state}>
							<option value="">— Not set (don't filter by state) —</option>
							{#each US_STATES as st (st)}
								<option value={st}>{st}</option>
							{/each}
						</select>
						<small class="state-disclaimer">
							Some employers can only hire in certain states. When you pick yours, ingest
							drops postings whose "we can only hire in X, Y, Z" list leaves your state out.
							This is used <strong>only</strong> to filter jobs during ingest — it is stored
							locally in your own database, never sent anywhere, and never used for any other
							purpose. Leave it unset to skip state filtering entirely.
						</small>
					</div>
					<div class="grid-2" style="margin-top:14px">
						<div class="field">
							<span>Title keywords <span style="color:var(--faint);font-weight:500">(gate)</span></span>
							<textarea class="input" name="title_terms" rows="5" bind:value={title_terms}></textarea>
							<small>Title must contain one of these — the job itself, e.g. "project manager" or "engineer". Leave empty to skip.</small>
						</div>
						<div class="field">
							<span>Seniority terms <span style="color:var(--faint);font-weight:500">(gate)</span></span>
							<textarea class="input" name="seniority_terms" rows="5" bind:value={seniority_terms}></textarea>
							<small>Title must contain one of these (senior, staff, principal, lead).</small>
						</div>
					</div>
					<div class="field" style="margin-top:14px">
						<span>Role titles</span>
						<textarea class="input" name="role_titles" rows="4" bind:value={role_titles}></textarea>
						<small>Not used by the filter — context for AI scoring and drafting. e.g. "Senior Project Manager".</small>
					</div>
					<div class="grid-2" style="margin-top:14px">
						<div class="field">
							<span>Required tech <span style="color:var(--faint);font-weight:500">(any-of)</span></span>
							<textarea class="input" name="required_tech" rows="4" bind:value={required_tech}></textarea>
							<small>Posting must reference at least one. Short tokens (≤2 chars) only flag as manual.</small>
						</div>
						<div class="field">
							<span>Excluded tech</span>
							<textarea class="input" name="excluded_tech" rows="4" bind:value={excluded_tech}></textarea>
							<small>Disqualifies when in title, or in tags without a required-tech tag.</small>
						</div>
					</div>
					<div class="field" style="margin-top:14px">
						<span>Skills detected <span style="color:var(--faint);font-weight:500">(reference)</span></span>
						<textarea class="input" name="extracted_skills" rows="4" bind:value={extracted_skills}></textarea>
						<small>Free-form notes from resume analysis. Not used by the filter directly.</small>
					</div>
					<div style="margin-top:16px">
						<button type="submit" class="btn primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
					</div>
				</div>
			</form>
		</div>

		<CompanyCoverageCard
			coverage={data.coverage}
			error={form && 'coverageError' in form ? form.coverageError : undefined}
		/>

		<WatchedCompaniesCard
			watched={data.watched}
			error={form && 'companyError' in form ? form.companyError : undefined}
			message={form?.companyOk && 'companyMessage' in form ? form.companyMessage || undefined : undefined}
			already={Boolean(form && 'companyAlready' in form && form.companyAlready)}
		/>

		<BlacklistCard
			entries={data.blacklist}
			error={form && 'blacklistError' in form ? form.blacklistError : undefined}
			message={form?.blacklistOk && 'blacklistMessage' in form ? form.blacklistMessage || undefined : undefined}
		/>
	</div>
</div>

<style>
	.state-select {
		max-width: 320px;
	}
	.state-disclaimer {
		line-height: 1.5;
		max-width: 60ch;
	}
	.rec-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-top: 16px;
	}
	.rec-actions form {
		margin: 0;
	}
	.criteria-actions {
		margin-left: auto;
	}
	.criteria-actions form {
		margin: 0;
	}
</style>
