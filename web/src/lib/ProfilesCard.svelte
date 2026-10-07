<script lang="ts">
	// The /search "Profiles" card: list, rename, switch to, delete, and add
	// profiles. The forms post to /search's own actions (?/createProfile,
	// ?/activateProfile, ?/updateProfileMeta, ?/deleteProfile), so this only
	// renders on that page; the page passes the action's error/message back in.
	import { enhance } from '$app/forms';
	import { whileBusy } from '$lib/busy';
	import type { SearchProfile } from '$lib/api';

	let {
		profiles,
		error = undefined,
		message = undefined
	}: { profiles: SearchProfile[]; error?: string; message?: string } = $props();

	let profileBusy = $state(false);
	const busyEnhance = whileBusy((b) => (profileBusy = b));
</script>

<div class="card">
	<div class="card-h" id="profiles"><h2>Profiles</h2></div>
	<div class="card-b">
		<p class="muted" style="margin-bottom:14px">
			One profile per person (or per kind of search), each with its own resumes, criteria,
			statuses, scores, and drafts. A scrape runs once for everyone and then fills each
			profile's queue by its own criteria. Adding a profile or changing its criteria
			re-checks the jobs already found, so there's no need to scrape again. Switch who's
			using the app from your name at the bottom of the sidebar; each person uploads and
			picks their own resumes on the <a href="/resume">Resume</a> page.
		</p>

		{#if error}
			<p class="err-text" style="margin-bottom:10px">{error}</p>
		{/if}
		{#if message}
			<p class="banner ok" style="margin-bottom:10px">{message}</p>
		{/if}

		<ul class="pf-list">
			{#each profiles as p (p.id)}
				<li class:pf-active={p.is_active}>
					<form method="POST" action="?/updateProfileMeta" class="pf-meta" use:enhance={busyEnhance}>
						<input type="hidden" name="id" value={p.id} />
						<input
							class="input pf-name"
							type="text"
							name="name"
							value={p.name}
							aria-label="Profile name"
							maxlength="80"
							required
						/>
						<button type="submit" class="btn sm" disabled={profileBusy}>Rename</button>
					</form>
					<span class="pf-resume" class:muted={!p.resume_filename}>
						{#if p.resume_filename}
							<span class="mono">{p.resume_filename}</span>
						{:else}
							No resume yet
						{/if}
						{#if p.is_active}<a href="/resume">Manage</a>{/if}
					</span>
					{#if p.is_active}
						<span class="pill pf-pill">Active</span>
					{:else}
						<form method="POST" action="?/activateProfile" use:enhance={busyEnhance}>
							<input type="hidden" name="id" value={p.id} />
							<button type="submit" class="btn sm primary" disabled={profileBusy}>Switch to</button>
						</form>
						<form method="POST" action="?/deleteProfile" use:enhance={busyEnhance}>
							<input type="hidden" name="id" value={p.id} />
							<button
								type="submit"
								class="btn ghost sm"
								disabled={profileBusy}
								aria-label="Delete {p.name}"
								onclick={(e) => {
									if (
										!confirm(
											`Delete "${p.name}"? This permanently removes its resumes, statuses, notes, scores, blacklist, and tailored drafts. Jobs are kept.`
										)
									)
										e.preventDefault();
								}}>Delete</button
							>
						</form>
					{/if}
				</li>
			{/each}
		</ul>

		<form method="POST" action="?/createProfile" class="pf-new" use:enhance={busyEnhance}>
			<input
				class="input"
				type="text"
				name="name"
				placeholder="New profile name"
				autocomplete="off"
				maxlength="80"
				required
			/>
			<select class="input pf-clone" name="clone_from" aria-label="Start new profile from">
				<option value="">Start blank (new person)</option>
				{#each profiles as p (p.id)}
					<option value={p.id}>Copy of {p.name}</option>
				{/each}
			</select>
			<button type="submit" class="btn" disabled={profileBusy}>Add profile</button>
		</form>
	</div>
</div>

<style>
	.pf-list {
		list-style: none;
		padding: 0;
		margin: 0 0 14px;
	}
	.pf-list li {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		padding: 10px 0;
		border-bottom: 1px solid var(--border);
	}
	.pf-list li form {
		margin: 0;
	}
	.pf-meta {
		display: flex;
		align-items: center;
		gap: 8px;
		flex: 1;
		min-width: 0;
		flex-wrap: wrap;
	}
	.pf-name {
		width: 200px;
	}
	.pf-active .pf-name {
		font-weight: 600;
	}
	.pf-resume {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 12.5px;
	}
	.pf-resume a {
		margin-left: 8px;
	}
	.pf-pill {
		color: var(--accent);
		border-color: var(--accent);
	}
	.pf-new {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.pf-new .input {
		flex: 1;
		min-width: 160px;
	}
	.pf-new .pf-clone {
		flex: 0 1 220px;
	}
</style>
