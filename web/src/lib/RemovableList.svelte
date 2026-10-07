<script lang="ts" generics="T extends { id: number }">
	// A list of entries, each with a Remove button that posts the entry's id to
	// `action` (the host page's form action). Shared by the /search blacklist and
	// watched-companies cards. `row` renders an entry's text; give its parts the
	// `rl-name` / `rl-sub` classes for the shared look.
	import { enhance } from '$app/forms';
	import type { Snippet } from 'svelte';

	let {
		items,
		action,
		label,
		row
	}: {
		items: T[];
		action: string;
		/** The entry's name, for the Remove button's accessible label. */
		label: (item: T) => string;
		row: Snippet<[T]>;
	} = $props();
</script>

<ul class="rl">
	{#each items as item (item.id)}
		<li>
			<div class="rl-main">{@render row(item)}</div>
			<form method="POST" {action} use:enhance>
				<input type="hidden" name="id" value={item.id} />
				<button type="submit" class="btn ghost sm rl-remove" aria-label="Remove {label(item)}"
					>Remove</button
				>
			</form>
		</li>
	{/each}
</ul>

<style>
	.rl {
		list-style: none;
		padding: 0;
		margin: 14px 0 0;
	}
	.rl li {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 10px 0;
		border-bottom: 1px solid var(--border);
	}
	.rl li:last-child {
		border-bottom: 0;
		padding-bottom: 0;
	}
	.rl form {
		margin: 0;
	}
	.rl-main {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.rl-main :global(.rl-name) {
		font-weight: 600;
		font-size: 13px;
		word-break: break-word;
	}
	.rl-main :global(.rl-sub) {
		font-size: 12px;
		color: var(--faint);
		word-break: break-word;
	}
	.rl-remove {
		flex-shrink: 0;
	}
</style>
