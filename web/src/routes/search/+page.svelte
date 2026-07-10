<script lang="ts">
	import { enhance } from '$app/forms';
	import { untrack } from 'svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let profile = $derived(form?.profile ?? data.profile);
	let saving = $state(false);

	let role_titles = $state(untrack(() => joinList(data.profile.role_titles)));
	let seniority_terms = $state(untrack(() => joinList(data.profile.seniority_terms)));
	let required_tech = $state(untrack(() => joinList(data.profile.required_tech)));
	let excluded_tech = $state(untrack(() => joinList(data.profile.excluded_tech)));
	let extracted_skills = $state(untrack(() => joinList(data.profile.extracted_skills)));

	let lastSeen = $state(untrack(() => data.profile.updated_at));
	$effect(() => {
		if (profile.updated_at && profile.updated_at !== lastSeen) {
			role_titles = joinList(profile.role_titles);
			seniority_terms = joinList(profile.seniority_terms);
			required_tech = joinList(profile.required_tech);
			excluded_tech = joinList(profile.excluded_tech);
			extracted_skills = joinList(profile.extracted_skills);
			lastSeen = profile.updated_at;
		}
	});

	function joinList(items: string[]): string {
		return items.join('\n');
	}

	let draft = $derived(profile.recommendations_draft);
	const hasProvider = $derived(Boolean(data.aiProvider));
	let suggesting = $state(false);
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>Search profile</h1>
		<div class="vh-sub">What the ingest filter keeps. One entry per line — commas also work.</div>
	</div>
	<div class="vh-actions">
		{#if !hasProvider}
			<a class="btn danger" href="/settings" title="Select an AI CLI in Settings">Suggest roles — set up AI</a>
		{:else}
			<form
				method="POST"
				action="?/suggest"
				use:enhance={() => {
					suggesting = true;
					return async ({ update }) => {
						await update();
						suggesting = false;
					};
				}}
			>
				<button type="submit" class="btn" disabled={suggesting || !data.hasResume}>
					{suggesting ? 'Analyzing resume…' : 'Suggest roles from resume'}
				</button>
			</form>
		{/if}
		<button type="submit" form="save-form" class="btn primary" disabled={saving}>{saving ? 'Saving…' : 'Save criteria'}</button>
	</div>
</div>

<div class="view-body">
	<div class="stack">
		{#if profile.using_defaults}
			<p class="banner info">
				No profile saved yet — filter is using built-in defaults.
				{#if data.hasResume}
					Use the Suggest-roles button for recommendations.
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

		<form
			id="save-form"
			method="POST"
			action="?/save"
			use:enhance={() => {
				saving = true;
				return async ({ update }) => {
					await update();
					saving = false;
				};
			}}
		>
			<div class="card">
				<div class="card-h"><h2>Active criteria</h2></div>
				<div class="card-b">
					<div class="grid-2">
						<div class="field">
							<span>Role titles</span>
							<textarea class="input" name="role_titles" rows="5" bind:value={role_titles}></textarea>
							<small>Documentation + LLM context. e.g. "Senior Software Engineer".</small>
						</div>
						<div class="field">
							<span>Seniority terms <span style="color:var(--faint);font-weight:500">(gate)</span></span>
							<textarea class="input" name="seniority_terms" rows="5" bind:value={seniority_terms}></textarea>
							<small>Title must contain one of these (senior, staff, principal, lead).</small>
						</div>
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
			</div>
		</form>
	</div>
</div>

<style>
	.rec-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-top: 16px;
	}
	.rec-actions form {
		margin: 0;
	}
</style>
