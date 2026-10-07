<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import Icon from '$lib/Icon.svelte';
	import type { SearchProfile } from '$lib/api';
	import { activeProfile } from '$lib/profiles';
	import { toast } from '$lib/toast.svelte';
	import { NAV, activeNavId, type CountKey } from './nav';
	import { deriveInitials, type ShellProfile } from './profile';

	let {
		counts = {},
		profile = null,
		profiles = []
	}: {
		counts?: Partial<Record<CountKey, number | null>>;
		/** Who the active resume says this is (name + headline), or null with no resume. */
		profile?: ShellProfile | null;
		/** Every saved profile, for the switcher menu. */
		profiles?: SearchProfile[];
	} = $props();

	const active = $derived(activeProfile(profiles));
	// Under the profile's name: whose resume it is, else their headline.
	const chipSub = $derived.by(() => {
		if (!profile) return 'No resume yet';
		if (active && profile.name.toLowerCase() !== active.name.toLowerCase()) return profile.name;
		return profile.subtitle ?? 'Resume on file';
	});
	let menuOpen = $state(false);
	let switching = $state(false);

	let activeId = $derived(activeNavId(page.url.pathname));
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (menuOpen = false)} />

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
	{#if active}
		<!-- The person using the app, and the way to switch to someone else.
		     Switching is a mutation, so each choice submits the /search form
		     action (server-side), which redirects back to this page. -->
		<div class="pm">
			{#if menuOpen}
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
				<div class="pm-scrim" onclick={() => (menuOpen = false)}></div>
				<div class="pm-menu" id="profile-menu" role="menu" aria-label="Profiles">
					<div class="pm-label">Switch profile</div>
					<form
						method="POST"
						action="/search?/activateProfile"
						use:enhance={() => {
							switching = true;
							return async ({ result, update }) => {
								// Only /search renders the action's error, so say it here too.
								if (result.type === 'failure' || result.type === 'error') {
									const reason =
										result.type === 'failure'
											? (result.data?.profileError as string | undefined)
											: undefined;
									toast(reason ?? 'Could not switch profile.');
								}
								await update();
								switching = false;
								menuOpen = false;
							};
						}}
					>
						<input type="hidden" name="redirect_to" value={page.url.pathname + page.url.search} />
						{#each profiles as p (p.id)}
							<button
								type="submit"
								name="id"
								value={p.id}
								class="pm-item"
								role="menuitemradio"
								aria-checked={p.is_active}
								disabled={p.is_active || switching}
							>
								<span class="avatar pm-av">{deriveInitials(p.name)}</span>
								<span class="pm-name">{p.name}</span>
								{#if p.is_active}<Icon name="check" size={14} stroke={2.2} />{/if}
							</button>
						{/each}
					</form>
					<div class="pm-sep"></div>
					<a class="pm-item" role="menuitem" href="/resume" onclick={() => (menuOpen = false)}>
						<Icon name="doc" size={15} /><span class="pm-name">{active.name}'s resumes</span>
					</a>
					<a class="pm-item" role="menuitem" href="/search#profiles" onclick={() => (menuOpen = false)}>
						<Icon name="plus" size={15} /><span class="pm-name">Add or manage profiles</span>
					</a>
				</div>
			{/if}
			<button
				type="button"
				class="user-chip pm-trigger"
				aria-haspopup="menu"
				aria-expanded={menuOpen}
				aria-controls="profile-menu"
				title="Switch profile"
				onclick={() => (menuOpen = !menuOpen)}
			>
				<div class="avatar">{deriveInitials(active.name)}</div>
				<div class="pm-who">
					<div class="u-name">{active.name}</div>
					<div class="u-sub">{chipSub}</div>
				</div>
				<span class="pm-caret"><Icon name="chevron" size={14} /></span>
			</button>
		</div>
	{:else if profile}
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
</aside>

<style>
	.count.due {
		color: var(--weak);
	}
	.pm {
		position: relative;
	}
	.pm-trigger {
		width: 100%;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}
	.pm-who {
		min-width: 0;
		flex: 1;
	}
	.pm-caret {
		display: grid;
		color: var(--faint);
		transform: rotate(-90deg);
		transition: transform 0.15s;
	}
	.pm-trigger[aria-expanded='true'] .pm-caret {
		transform: rotate(90deg);
	}
	.pm-scrim {
		position: fixed;
		inset: 0;
		z-index: 40;
	}
	.pm-menu {
		position: absolute;
		left: 0;
		right: 0;
		bottom: calc(100% + 6px);
		z-index: 41;
		padding: 6px;
		border: 1px solid var(--border);
		border-radius: 10px;
		background: var(--surface);
		box-shadow: 0 10px 30px rgb(0 0 0 / 0.18);
	}
	.pm-label {
		padding: 4px 8px 6px;
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
	}
	.pm-item {
		display: flex;
		align-items: center;
		gap: 9px;
		width: 100%;
		padding: 7px 8px;
		border: 0;
		border-radius: 7px;
		background: none;
		color: inherit;
		font: inherit;
		font-size: 12.5px;
		text-align: left;
		text-decoration: none;
		cursor: pointer;
	}
	.pm-item:hover:not(:disabled) {
		background: var(--surface-2);
		text-decoration: none;
	}
	.pm-item:disabled {
		cursor: default;
		font-weight: 600;
	}
	.pm-name {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.pm-av {
		width: 22px;
		height: 22px;
		font-size: 10px;
	}
	.pm-sep {
		height: 1px;
		margin: 6px 4px;
		background: var(--border);
	}
</style>
