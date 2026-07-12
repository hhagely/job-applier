<script lang="ts">
	// Shared AI-provider picker used by /settings and the onboarding wizard: the
	// "no CLI detected" banner, or a radio list of detected providers. Presentational
	// only — `bind:chosen` surfaces the selection; each parent owns the surrounding
	// form / buttons / extra fields (Ollama model, scoring model) and the mutation.
	import type { Provider } from '$lib/api';

	let {
		providers,
		anyAvailable,
		chosen = $bindable()
	}: {
		providers: Provider[];
		anyAvailable: boolean;
		chosen: string | null | undefined;
	} = $props();
</script>

{#if !anyAvailable}
	<div class="banner warn">
		<strong>No AI CLI detected.</strong>
		<p style="margin:6px 0">Install one of these to enable scoring &amp; drafting, then reload:</p>
		<ul class="install-list">
			<li>
				<a href="https://docs.claude.com/en/docs/claude-code" target="_blank" rel="noopener">Claude Code</a>
				(recommended)
			</li>
			<li>
				<a href="https://github.com/google-gemini/gemini-cli" target="_blank" rel="noopener">Gemini CLI</a>
				(recommended)
			</li>
			<li><a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a> (local, best-effort)</li>
		</ul>
		<p class="muted" style="margin:0">
			The rest of the app (ingest, filter, browse, track, PDF export) works without any AI CLI.
		</p>
	</div>
{:else}
	<ul class="providers">
		{#each providers as p (p.name)}
			<li class:unavailable={!p.available}>
				<label>
					<input
						type="radio"
						name="name"
						value={p.name}
						bind:group={chosen}
						disabled={!p.available}
					/>
					<span class="pname">{p.display_name}</span>
					<span class="badge {p.tier}">{p.tier}</span>
					{#if p.available}
						<span class="ok">✓ {p.version ?? 'detected'}</span>
					{:else}
						<span class="muted">not installed</span>
					{/if}
				</label>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.install-list {
		margin: 0 0 6px 1.1rem;
	}
	.providers {
		list-style: none;
		padding: 0;
		margin: 0 0 14px;
	}
	.providers li {
		padding: 8px 0;
		border-bottom: 1px solid var(--border);
	}
	.providers li:last-child {
		border-bottom: 0;
	}
	.providers li.unavailable {
		opacity: 0.55;
	}
	.providers label {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		cursor: pointer;
	}
	.pname {
		font-weight: 600;
	}
	.badge {
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		padding: 0.05rem 0.4rem;
		border-radius: 10px;
		border: 1px solid var(--border-2);
		color: var(--muted);
	}
	.badge.recommended {
		color: var(--strong);
		border-color: var(--strong);
	}
	.ok {
		color: var(--strong);
		font-size: 0.85rem;
	}
</style>
