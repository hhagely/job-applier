<script lang="ts">
	import { page } from '$app/state';
	import Icon from '$lib/Icon.svelte';
	import { NAV, activeNavId, type CountKey } from './nav';
	import type { ShellProfile } from './profile';

	let {
		counts = {},
		profile = null
	}: {
		counts?: Partial<Record<CountKey, number | null>>;
		profile?: ShellProfile | null;
	} = $props();

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
</aside>

<style>
	.count.due {
		color: var(--weak);
	}
</style>
