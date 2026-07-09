<script lang="ts">
	import { page } from '$app/state';
	import Icon from '$lib/Icon.svelte';
	import { NAV, activeNavId, type CountKey } from './nav';

	let { counts = {} }: { counts?: Partial<Record<CountKey, number | null>> } = $props();

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
	<div class="user-chip">
		<div class="avatar">HH</div>
		<div style="min-width:0">
			<div class="u-name">Herb Hagely</div>
			<div class="u-sub">Senior SWE · St. Louis</div>
		</div>
	</div>
</aside>

<style>
	.count.due {
		color: var(--weak);
	}
</style>
