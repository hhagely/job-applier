<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// Prefer the freshest provider list: an action that changed selection returns
	// an updated `ai`; otherwise use the loader's.
	let ai = $derived(form?.ok && 'ai' in form && form.ai ? form.ai : data.ai);
	let providers = $derived(ai.providers);
	let anyAvailable = $derived(providers.some((p) => p.available));

	// Selected radio value (defaults to the persisted selection or first available).
	let chosen = $state('');
	$effect(() => {
		if (!chosen) {
			chosen = ai.selected ?? providers.find((p) => p.available)?.name ?? '';
		}
	});

	let testResult = $derived(form && 'test' in form ? form.test : null);
	let testing = $state(false);
</script>

<h1>Settings</h1>

<section class="panel">
	<h2>AI provider</h2>
	<p class="muted">
		Scoring and drafting run through an AI CLI you already have installed, on your own
		subscription. The app never calls a vendor API or handles keys. Prompts are sent
		sandboxed (no tools, no file access).
	</p>

	{#if !anyAvailable}
		<div class="empty">
			<strong>No AI CLI detected.</strong>
			<p>
				Install one of these to enable scoring &amp; drafting, then reload:
			</p>
			<ul>
				<li>
					<a href="https://docs.claude.com/en/docs/claude-code" target="_blank" rel="noopener">
						Claude Code</a
					> (recommended)
				</li>
				<li>
					<a href="https://github.com/google-gemini/gemini-cli" target="_blank" rel="noopener">
						Gemini CLI</a
					> (recommended)
				</li>
				<li>
					<a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a> (local, best-effort)
				</li>
			</ul>
			<p class="muted">
				The rest of the app (ingest, filter, browse, track, PDF export) works without any AI
				CLI.
			</p>
		</div>
	{:else}
		<form method="POST" action="?/select" use:enhance>
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

			{#if chosen === 'ollama'}
				<label class="model-row">
					Model
					<input type="text" name="model" value={ai.model ?? ''} placeholder="llama3.1" />
				</label>
			{/if}

			<button type="submit" disabled={!chosen}>Save selection</button>
		</form>

		{#if ai.selected}
			<p class="selected">
				Active: <strong>{ai.selected}</strong>
			</p>
		{/if}
	{/if}

	{#if form?.error}
		<p class="error">{form.error}</p>
	{/if}
	{#if form?.ok && 'message' in form && form.message}
		<p class="info-banner">{form.message}</p>
	{/if}
</section>

{#if anyAvailable && ai.selected}
	<section class="panel">
		<h2>Test round-trip</h2>
		<p class="muted">
			Sends a trivial prompt through <strong>{ai.selected}</strong> and shows the raw
			response. Proves the sandbox works before scoring or drafting depends on it.
		</p>
		<form
			method="POST"
			action="?/test"
			use:enhance={() => {
				testing = true;
				return async ({ update }) => {
					await update();
					testing = false;
				};
			}}
		>
			<input type="text" name="prompt" placeholder="Respond with exactly the word: pong" />
			<button type="submit" disabled={testing}>{testing ? 'Running…' : 'Test'}</button>
		</form>

		{#if testResult}
			{#if testResult.ok}
				<pre class="test-out">{testResult.output}</pre>
			{:else}
				<p class="error">Failed: {testResult.error}</p>
			{/if}
		{/if}
	</section>
{/if}

<style>
	h1 {
		margin: 0 0 1rem;
		font-size: 1.5rem;
	}
	.panel {
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
		padding: 1rem 1.25rem;
		margin-bottom: 1rem;
	}
	.panel h2 {
		font-size: 1rem;
		margin: 0 0 0.5rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.muted {
		color: var(--muted);
		margin: 0 0 0.75rem;
	}
	.providers {
		list-style: none;
		padding: 0;
		margin: 0 0 1rem;
	}
	.providers li {
		padding: 0.4rem 0;
		border-bottom: 1px solid var(--panel-border);
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
		border: 1px solid var(--panel-border);
		color: var(--muted);
	}
	.badge.recommended {
		color: var(--ok);
		border-color: var(--ok);
	}
	.ok {
		color: var(--ok);
		font-size: 0.85rem;
	}
	.model-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
	}
	.model-row input {
		flex: 1;
		max-width: 220px;
	}
	.selected {
		margin: 0.5rem 0 0;
	}
	.empty {
		padding: 0.75rem 1rem;
		background: rgba(210, 153, 34, 0.1);
		border: 1px solid var(--warn);
		border-radius: 6px;
	}
	.empty ul {
		margin: 0.5rem 0;
	}
	.error {
		color: var(--bad);
		margin-top: 0.5rem;
	}
	.info-banner {
		margin-top: 0.75rem;
		padding: 0.5rem 0.75rem;
		background: rgba(88, 166, 255, 0.1);
		border: 1px solid var(--accent);
		border-radius: 6px;
		font-size: 0.9rem;
	}
	.test-out {
		margin-top: 0.75rem;
		padding: 0.6rem 0.8rem;
		background: var(--bg);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		white-space: pre-wrap;
		word-break: break-word;
	}
	input[type='text'] {
		background: var(--bg);
		color: var(--fg);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.35rem 0.5rem;
	}
	button {
		background: var(--accent);
		color: #08131f;
		border: 0;
		border-radius: 6px;
		padding: 0.4rem 0.9rem;
		font-weight: 600;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
