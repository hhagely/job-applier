<script lang="ts">
	// The app's one confirmation prompt, in place of the native confirm(). It only
	// asks: the caller decides what confirming does and closes it by flipping `open`.
	import type { Snippet } from 'svelte';

	let {
		open = false,
		title,
		confirmLabel,
		danger = false,
		onConfirm,
		onCancel,
		children
	}: {
		open?: boolean;
		title: string;
		confirmLabel: string;
		danger?: boolean;
		onConfirm: () => void;
		onCancel: () => void;
		children: Snippet;
	} = $props();

	const id = $props.id();
	let cancelEl = $state<HTMLButtonElement | null>(null);
	let confirmEl = $state<HTMLButtonElement | null>(null);

	// Start on Cancel so a stray Enter can't take the action, then hand focus back
	// to whatever opened the dialog.
	$effect(() => {
		if (!open) return;
		const trigger = document.activeElement;
		cancelEl?.focus();
		return () => {
			if (trigger instanceof HTMLElement) trigger.focus();
		};
	});

	// On capture, so the page's own shortcuts (J/K, ?, /, Ctrl+K) can't act behind
	// the open dialog. Nothing here prevents Enter/Space from clicking a button.
	function onKeydown(e: KeyboardEvent) {
		if (!open) return;
		e.stopPropagation();
		if (e.key === 'Escape') {
			e.preventDefault();
			onCancel();
		} else if (e.key === 'Tab') {
			// Only two stops, so Tab and Shift+Tab are the same move.
			e.preventDefault();
			(document.activeElement === cancelEl ? confirmEl : cancelEl)?.focus();
		}
	}
</script>

<svelte:window onkeydowncapture={onKeydown} />

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div class="scrim" onclick={(e) => e.target === e.currentTarget && onCancel()}>
		<div
			class="sheet"
			role="dialog"
			aria-modal="true"
			aria-labelledby="{id}-title"
			aria-describedby="{id}-body"
		>
			<div class="sheet-h"><h3 id="{id}-title">{title}</h3></div>
			<div class="confirm-body" id="{id}-body">{@render children()}</div>
			<div class="confirm-actions">
				<button type="button" class="btn" bind:this={cancelEl} onclick={onCancel}>Cancel</button>
				<button
					type="button"
					class="btn"
					class:primary={!danger}
					class:danger
					bind:this={confirmEl}
					onclick={onConfirm}>{confirmLabel}</button
				>
			</div>
		</div>
	</div>
{/if}

<style>
	.confirm-body {
		padding: 16px 18px;
		font-size: 13px;
		line-height: 1.5;
		color: var(--muted);
	}
	.confirm-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 0 18px 18px;
	}
</style>
