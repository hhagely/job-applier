<script lang="ts">
	import { enhance } from '$app/forms';
	import { untrack } from 'svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let profile = $derived(form?.profile ?? data.profile);
	let saving = $state(false);

	// Seed inputs once from the initial server load; $effect below re-seeds them
	// when the server returns a fresher profile (e.g. after accepting drafts).
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

<h1>Search profile</h1>

<p class="muted">
	What kinds of jobs the ingest filter should keep. Lists below are one entry per
	line (commas also work). Empty <strong>required tech</strong> or
	<strong>seniority</strong> falls back to built-in defaults so nothing breaks.
</p>

{#if profile.using_defaults}
	<p class="info-banner">
		No profile saved yet — filter is using built-in defaults.
		{#if data.hasResume}
			Run <code>/suggest-roles</code> in Claude Code to generate recommendations from your resume.
		{:else}
			Upload a resume first, then run <code>/suggest-roles</code> in Claude Code for recommendations.
		{/if}
	</p>
{/if}

<div class="suggest-row">
	{#if !hasProvider}
		<a class="suggest-link" href="/settings" title="Select an AI CLI in Settings">
			Suggest roles — set up AI
		</a>
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
			<button type="submit" class="suggest-btn" disabled={suggesting || !data.hasResume}>
				{suggesting ? 'Analyzing resume…' : 'Suggest roles from resume'}
			</button>
		</form>
		{#if !data.hasResume}
			<span class="muted">Upload a resume first.</span>
		{/if}
	{/if}
</div>

{#if form?.message}
	<p class="ok-banner">{form.message}</p>
{/if}
{#if form?.error}
	<p class="error">{form.error}</p>
{/if}

{#if draft}
	<section class="panel draft">
		<h2>Recommendations</h2>
		{#if draft.rationale}
			<p class="muted">{draft.rationale}</p>
		{/if}
		<dl>
			<dt>Role titles</dt>
			<dd>{draft.role_titles.join(', ') || '—'}</dd>
			<dt>Seniority</dt>
			<dd>{draft.seniority_terms.join(', ') || '—'}</dd>
			<dt>Required tech</dt>
			<dd>{draft.required_tech.join(', ') || '—'}</dd>
			<dt>Excluded tech</dt>
			<dd>{draft.excluded_tech.join(', ') || '—'}</dd>
			<dt>Skills detected</dt>
			<dd>{draft.extracted_skills.join(', ') || '—'}</dd>
		</dl>
		<div class="actions">
			<form
				method="POST"
				action="?/acceptDraft"
				use:enhance={() => {
					return async ({ update }) => {
						await update();
					};
				}}
			>
				<input type="hidden" name="mode" value="replace" />
				<button type="submit" class="primary">Replace with these</button>
			</form>
			<form method="POST" action="?/acceptDraft" use:enhance>
				<input type="hidden" name="mode" value="append" />
				<button type="submit">Add to current</button>
			</form>
			<form method="POST" action="?/rejectDraft" use:enhance>
				<button type="submit" class="ghost">Dismiss</button>
			</form>
		</div>
	</section>
{/if}

<form
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
	<section class="panel">
		<h2>Active criteria</h2>

		<label>
			<span>Role titles</span>
			<textarea name="role_titles" rows="4" bind:value={role_titles}></textarea>
			<small>e.g. "Senior Software Engineer". Used as documentation + LLM context.</small>
		</label>

		<label>
			<span>Seniority terms (gate)</span>
			<textarea name="seniority_terms" rows="3" bind:value={seniority_terms}></textarea>
			<small>Title must contain one of these (e.g. senior, staff, principal, lead).</small>
		</label>

		<label>
			<span>Required tech (any-of)</span>
			<textarea name="required_tech" rows="4" bind:value={required_tech}></textarea>
			<small>Posting must reference at least one. Short tokens (≤2 chars) only flag as manual.</small>
		</label>

		<label>
			<span>Excluded tech</span>
			<textarea name="excluded_tech" rows="2" bind:value={excluded_tech}></textarea>
			<small>Disqualifies when in title, or in tags without a required-tech tag.</small>
		</label>

		<label>
			<span>Skills detected (reference)</span>
			<textarea name="extracted_skills" rows="4" bind:value={extracted_skills}></textarea>
			<small>Free-form notes from resume analysis. Not used by the filter directly.</small>
		</label>

		<div class="actions">
			<button type="submit" disabled={saving} class="primary">
				{saving ? 'Saving…' : 'Save'}
			</button>
		</div>
	</section>
</form>

<style>
	h1 {
		margin: 0 0 0.5rem;
		font-size: 1.5rem;
	}
	.muted {
		color: var(--muted);
		margin: 0 0 1rem;
	}
	.suggest-row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-bottom: 1rem;
	}
	.suggest-btn {
		background: var(--accent);
		color: #0d1117;
		border: 0;
		border-radius: 6px;
		padding: 0.4rem 0.9rem;
		font-weight: 600;
		cursor: pointer;
	}
	.suggest-btn:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.suggest-link {
		color: var(--warn);
		border: 1px solid var(--warn);
		border-radius: 6px;
		padding: 0.4rem 0.9rem;
		font-weight: 600;
	}
	.suggest-link:hover {
		text-decoration: none;
		filter: brightness(1.1);
	}
	.panel {
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1rem;
	}
	.panel h2 {
		font-size: 1rem;
		margin: 0 0 0.75rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.draft {
		border-color: var(--accent);
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		margin-bottom: 0.9rem;
	}
	label span {
		font-size: 0.9rem;
		color: var(--fg);
	}
	label small {
		color: var(--muted);
		font-size: 0.8rem;
	}
	textarea {
		font: inherit;
		background: var(--bg);
		color: var(--fg);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.5rem 0.6rem;
		resize: vertical;
	}
	textarea:focus {
		outline: 1px solid var(--accent);
		border-color: var(--accent);
	}
	dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		column-gap: 1rem;
		row-gap: 0.3rem;
		margin: 0 0 1rem;
	}
	dt {
		color: var(--muted);
		font-size: 0.85rem;
	}
	dd {
		margin: 0;
		font-size: 0.9rem;
	}
	.actions {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.actions form {
		margin: 0;
	}
	button {
		font: inherit;
		color: var(--fg);
		background: var(--panel-border);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.4rem 0.9rem;
		cursor: pointer;
	}
	button:hover {
		border-color: var(--accent);
	}
	button.primary {
		background: var(--accent);
		color: var(--bg);
		border-color: var(--accent);
	}
	button.ghost {
		background: transparent;
	}
	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.info-banner {
		margin: 0 0 1rem;
		padding: 0.5rem 0.75rem;
		background: rgba(88, 166, 255, 0.1);
		border: 1px solid var(--accent);
		border-radius: 6px;
		color: var(--fg);
		font-size: 0.9rem;
	}
	.ok-banner {
		margin: 0 0 1rem;
		padding: 0.4rem 0.75rem;
		background: rgba(46, 160, 67, 0.15);
		border: 1px solid var(--ok);
		border-radius: 6px;
		color: var(--fg);
		font-size: 0.9rem;
	}
	.error {
		color: var(--bad);
		margin: 0 0 1rem;
	}
	code {
		background: var(--bg);
		padding: 0.05rem 0.3rem;
		border-radius: 3px;
	}
</style>
