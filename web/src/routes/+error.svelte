<script lang="ts">
	import { page } from '$app/state';
	import { invalidateAll } from '$app/navigation';

	let retrying = $state(false);

	// A page-loader failure usually means the backend was briefly unreachable
	// (uvicorn --reload restarting in dev). invalidateAll() re-runs the loaders
	// without a full navigation, so once the backend is back the page recovers
	// in place.
	async function retry() {
		retrying = true;
		try {
			await invalidateAll();
		} finally {
			retrying = false;
		}
	}

	const backendLikely = $derived(page.status >= 500 || page.status === 0);
</script>

<div class="error-wrap">
	<div class="card error-card">
		<div class="error-status">{page.status}</div>
		<h1>Something went wrong</h1>
		<p class="error-msg">{page.error?.message ?? 'Unexpected error.'}</p>
		{#if backendLikely}
			<p class="hint">
				The backend may be restarting (a Python edit in dev). Give it a second, then retry.
			</p>
		{/if}
		<button class="btn primary" onclick={retry} disabled={retrying}>
			{retrying ? 'Retrying…' : 'Try again'}
		</button>
	</div>
</div>

<style>
	.error-wrap {
		display: grid;
		place-items: center;
		min-height: 60vh;
		padding: 2rem;
	}
	.error-card {
		max-width: 30rem;
		display: grid;
		gap: 0.75rem;
		padding: 2rem;
		text-align: center;
	}
	.error-status {
		font-size: 2.5rem;
		font-weight: 700;
		line-height: 1;
		color: var(--danger);
	}
	.error-msg {
		color: var(--muted);
		word-break: break-word;
	}
	.hint {
		font-size: 0.85rem;
		color: var(--muted);
	}
	.error-card .btn {
		justify-self: center;
	}
</style>
