<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import Icon from '$lib/Icon.svelte';
	import type { SearchProfile } from '$lib/api';
	import { NAV, activeNavId, type CountKey } from './nav';
	import type { ShellProfile } from './profile';

	let {
		counts = {},
		profile = null,
		profiles = []
	}: {
		counts?: Partial<Record<CountKey, number | null>>;
		profile?: ShellProfile | null;
		/** Every saved profile; the switcher only shows once there are two. */
		profiles?: SearchProfile[];
	} = $props();

	const activeProfileId = $derived(profiles.find((p) => p.is_active)?.id ?? null);
	let switching = $state(false);

	let activeId = $derived(activeNavId(page.url.pathname));
</script>

<aside class="sidebar">
	<nav class="nav" aria-label="Primary">
		{#each NAV as item (item.id)}
			{#if item.group}
				<div class="side-label">{item.group}</div>
			{/if}
			<a
				href={item.href}
				class:active={activeId === item.id}
				aria-current={activeId === item.id ? 'page' : undefined}
			>
				<Icon name={item.icon} />
				<span>{item.label}</span>
				{#if item.countKey && counts[item.countKey] != null && counts[item.countKey]! > 0}
					<span class="count" class:due={item.countKey === 'followups'}>
						{counts[item.countKey]}
					</span>
				{/if}
			</a>
		{/each}
	</nav>
	<div class="side-spacer"></div>
	{#if profile}
		<a class="user-chip" href="/resume" title="Manage resume">
			<div class="avatar">{profile.initials}</div>
			<div style="min-width:0">
				<div class="u-name">{profile.name}</div>
				{#if profile.subtitle}
					<div class="u-sub">{profile.subtitle}</div>
				{/if}
			</div>
		</a>
	{:else}
		<a class="user-chip" href="/onboarding" title="Upload your resume">
			<div class="avatar empty"><Icon name="upload" size={15} /></div>
			<div style="min-width:0">
				<div class="u-name">No resume</div>
				<div class="u-sub">Upload to get started</div>
			</div>
		</a>
	{/if}
	{#if profiles.length > 1}
		<!-- Switching who is using the app is a mutation, so it goes through the
		     /search form action (server-side), which redirects back here. -->
		<form
			method="POST"
			action="/search?/activateProfile"
			class="profile-switch"
			use:enhance={() => {
				switching = true;
				return async ({ update }) => {
					await update();
					switching = false;
				};
			}}
		>
			<input type="hidden" name="redirect_to" value={page.url.pathname + page.url.search} />
			<label class="ps-label" for="profile-switch">Profile</label>
			<select
				id="profile-switch"
				class="mini-input ps-select"
				name="id"
				value={activeProfileId === null ? '' : String(activeProfileId)}
				disabled={switching}
				onchange={(e) => e.currentTarget.form?.requestSubmit()}
			>
				{#each profiles as p (p.id)}
					<option value={String(p.id)}>{p.name}</option>
				{/each}
			</select>
		</form>
	{/if}
</aside>

<style>
	.count.due {
		color: var(--weak);
	}
	.profile-switch {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-top: 8px;
		padding: 0 4px;
	}
	.ps-label {
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
	}
	.ps-select {
		flex: 1;
		min-width: 0;
	}
</style>
