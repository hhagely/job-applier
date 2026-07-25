<script lang="ts">
	import { toasts } from '$lib/toast.svelte';
</script>

{#if toasts.items.length}
	<div class="toaster" aria-live="polite">
		{#each toasts.items as t (t.id)}
			<div class="toast" role="status">
				<span>{t.message}</span>
				<button type="button" class="tx" aria-label="Dismiss" onclick={() => toasts.dismiss(t.id)}>✕</button>
			</div>
		{/each}
	</div>
{/if}

<style>
	.toaster {
		position: fixed;
		bottom: 44px;
		right: 16px;
		z-index: 200;
		display: flex;
		flex-direction: column;
		gap: 8px;
		max-width: 340px;
	}
	.toast {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 9px 12px;
		font-size: 12.5px;
		background: var(--surface);
		color: var(--fg);
		border: 1px solid var(--border);
		border-radius: 9px;
		box-shadow: 0 10px 30px -12px rgba(0, 0, 0, 0.5);
		animation: tin 0.16s ease;
	}
	@keyframes tin {
		from {
			opacity: 0;
			transform: translateY(6px);
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	.toast span {
		min-width: 0;
	}
	.tx {
		margin-left: auto;
		color: var(--muted);
		font-size: 11px;
		line-height: 1;
		padding: 3px 5px;
		border-radius: 5px;
		flex: none;
	}
	.tx:hover {
		background: var(--surface-2);
		color: var(--fg);
	}
</style>
