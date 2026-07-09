<script lang="ts">
	import { enhance } from '$app/forms';
	import { theme, type ThemePref } from '$lib/theme.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let ai = $derived(form?.ok && 'ai' in form && form.ai ? form.ai : data.ai);
	let providers = $derived(ai.providers);
	let anyAvailable = $derived(providers.some((p) => p.available));

	let chosen = $state('');
	$effect(() => {
		if (!chosen) chosen = ai.selected ?? providers.find((p) => p.available)?.name ?? '';
	});

	let testResult = $derived(form && 'test' in form ? form.test : null);
	let testing = $state(false);

	const THEMES: { key: ThemePref; label: string }[] = [
		{ key: 'system', label: 'System' },
		{ key: 'light', label: 'Light' },
		{ key: 'dark', label: 'Dark' }
	];
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>Settings</h1>
		<div class="vh-sub">App preferences · local-only</div>
	</div>
</div>

<div class="view-body">
	<div class="stack">
		<div class="card">
			<div class="card-h"><h2>Appearance</h2></div>
			<div class="card-b">
				<div class="set-row">
					<div class="sr-main">
						<div class="sr-t">Theme</div>
						<div class="sr-d">System follows your OS light/dark setting.</div>
					</div>
					<div class="theme-seg">
						{#each THEMES as t (t.key)}
							<button type="button" aria-pressed={theme.pref === t.key} onclick={() => theme.set(t.key)}>{t.label}</button>
						{/each}
					</div>
				</div>
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>AI provider</h2></div>
			<div class="card-b">
				<p class="muted" style="margin-bottom:12px">
					Scoring and drafting run through an AI CLI you already have installed, on your own
					subscription. The app never calls a vendor API or handles keys. Prompts are sent sandboxed
					(no tools, no file access).
				</p>

				{#if !anyAvailable}
					<div class="banner warn">
						<strong>No AI CLI detected.</strong>
						<p style="margin:6px 0">Install one of these to enable scoring &amp; drafting, then reload:</p>
						<ul style="margin:0 0 6px 1.1rem">
							<li><a href="https://docs.claude.com/en/docs/claude-code" target="_blank" rel="noopener">Claude Code</a> (recommended)</li>
							<li><a href="https://github.com/google-gemini/gemini-cli" target="_blank" rel="noopener">Gemini CLI</a> (recommended)</li>
							<li><a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a> (local, best-effort)</li>
						</ul>
						<p class="muted" style="margin:0">The rest of the app (ingest, filter, browse, track, PDF export) works without any AI CLI.</p>
					</div>
				{:else}
					<form method="POST" action="?/select" use:enhance>
						<ul class="providers">
							{#each providers as p (p.name)}
								<li class:unavailable={!p.available}>
									<label>
										<input type="radio" name="name" value={p.name} bind:group={chosen} disabled={!p.available} />
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
							<div class="field" style="max-width:280px;margin-bottom:12px">
								<span>Model</span>
								<input class="input" type="text" name="model" value={ai.model ?? ''} placeholder="llama3.1" />
							</div>
						{/if}

						<button type="submit" class="btn primary" disabled={!chosen}>Save selection</button>
					</form>

					{#if ai.selected}<p class="muted" style="margin-top:10px">Active: <strong style="color:var(--fg)">{ai.selected}</strong></p>{/if}
				{/if}

				{#if form?.error}<p class="err-text" style="margin-top:10px">{form.error}</p>{/if}
				{#if form?.ok && 'message' in form && form.message}<p class="banner info" style="margin-top:10px">{form.message}</p>{/if}
			</div>
		</div>

		{#if anyAvailable && ai.selected}
			<div class="card">
				<div class="card-h"><h2>Test round-trip</h2></div>
				<div class="card-b">
					<p class="muted" style="margin-bottom:12px">
						Sends a trivial prompt through <strong>{ai.selected}</strong> and shows the raw response.
						Proves the sandbox works before scoring or drafting depends on it.
					</p>
					<form
						method="POST"
						action="?/test"
						class="test-form"
						use:enhance={() => {
							testing = true;
							return async ({ update }) => {
								await update();
								testing = false;
							};
						}}
					>
						<input class="input" type="text" name="prompt" placeholder="Respond with exactly the word: pong" />
						<button type="submit" class="btn primary" disabled={testing}>{testing ? 'Running…' : 'Test'}</button>
					</form>
					{#if testResult}
						{#if testResult.ok}
							<pre class="test-out">{testResult.output}</pre>
						{:else}
							<p class="err-text" style="margin-top:10px">Failed: {testResult.error}</p>
						{/if}
					{/if}
				</div>
			</div>
		{/if}
	</div>
</div>

<style>
	.set-row {
		display: flex;
		align-items: center;
		gap: 14px;
		padding: 14px 0;
		border-bottom: 1px solid var(--border);
	}
	.set-row:first-child {
		padding-top: 0;
	}
	.set-row:last-child {
		border-bottom: 0;
		padding-bottom: 0;
	}
	.sr-main {
		flex: 1;
		min-width: 0;
	}
	.sr-t {
		font-weight: 600;
		font-size: 13px;
	}
	.sr-d {
		font-size: 12px;
		color: var(--faint);
		margin-top: 3px;
		line-height: 1.5;
	}
	.theme-seg {
		display: inline-flex;
		border: 1px solid var(--border-2);
		border-radius: 8px;
		overflow: hidden;
	}
	.theme-seg button {
		height: 30px;
		padding: 0 13px;
		font-size: 12px;
		color: var(--muted);
		font-weight: 560;
		border-right: 1px solid var(--border);
	}
	.theme-seg button:last-child {
		border-right: 0;
	}
	.theme-seg button[aria-pressed='true'] {
		background: var(--accent-soft);
		color: var(--accent);
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
	.test-form {
		display: flex;
		gap: 8px;
		align-items: center;
	}
	.test-form .input {
		flex: 1;
	}
	.test-out {
		margin-top: 12px;
		padding: 0.6rem 0.8rem;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 8px;
		white-space: pre-wrap;
		word-break: break-word;
		font-family: var(--mono);
		font-size: 12px;
	}
</style>
