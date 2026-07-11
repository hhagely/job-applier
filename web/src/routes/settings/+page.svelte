<script lang="ts">
	import { enhance } from '$app/forms';
	import { theme, type ThemePref } from '$lib/theme.svelte';
	import {
		appearance,
		type Accent,
		type ReadingFont,
		type ReadingSize,
		type ReadingSpacing,
		type ReadingWidth
	} from '$lib/appearance.svelte';
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

	// Accent swatches carry their own hue so the palette renders in true colors
	// regardless of the active accent; keep hues in sync with app.css.
	const ACCENTS: { key: Accent; label: string; hue: number }[] = [
		{ key: 'blue', label: 'Blue', hue: 258 },
		{ key: 'violet', label: 'Violet', hue: 300 },
		{ key: 'teal', label: 'Teal', hue: 200 },
		{ key: 'amber', label: 'Amber', hue: 75 },
		{ key: 'rose', label: 'Rose', hue: 12 }
	];
	const SIZES: { key: ReadingSize; label: string }[] = [
		{ key: 'sm', label: 'Small' },
		{ key: 'md', label: 'Default' },
		{ key: 'lg', label: 'Large' },
		{ key: 'xl', label: 'X-Large' }
	];
	const FONTS: { key: ReadingFont; label: string }[] = [
		{ key: 'sans', label: 'Sans' },
		{ key: 'serif', label: 'Serif' }
	];
	const SPACINGS: { key: ReadingSpacing; label: string }[] = [
		{ key: 'normal', label: 'Normal' },
		{ key: 'relaxed', label: 'Relaxed' }
	];
	const WIDTHS: { key: ReadingWidth; label: string }[] = [
		{ key: 'comfortable', label: 'Comfortable' },
		{ key: 'full', label: 'Full width' }
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
				<div class="set-row">
					<div class="sr-main">
						<div class="sr-t">Accent color</div>
						<div class="sr-d">Tints buttons, links, focus rings, and the active nav item.</div>
					</div>
					<div class="accent-swatches" role="radiogroup" aria-label="Accent color">
						{#each ACCENTS as a (a.key)}
							<button
								type="button"
								class="sw"
								class:on={appearance.accent === a.key}
								style="--sw:oklch(66% 0.15 {a.hue})"
								role="radio"
								aria-checked={appearance.accent === a.key}
								aria-label={a.label}
								title={a.label}
								onclick={() => appearance.set({ accent: a.key })}
							></button>
						{/each}
					</div>
				</div>
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>Reading</h2></div>
			<div class="card-b">
				<div class="set-row">
					<div class="sr-main">
						<div class="sr-t">Text size</div>
						<div class="sr-d">Scales job-description text (and the draft preview).</div>
					</div>
					<div class="theme-seg">
						{#each SIZES as s (s.key)}
							<button type="button" aria-pressed={appearance.readingSize === s.key} onclick={() => appearance.set({ readingSize: s.key })}>{s.label}</button>
						{/each}
					</div>
				</div>
				<div class="set-row">
					<div class="sr-main">
						<div class="sr-t">Reading font</div>
						<div class="sr-d">Serif can be easier for long-form reading.</div>
					</div>
					<div class="theme-seg">
						{#each FONTS as f (f.key)}
							<button type="button" aria-pressed={appearance.readingFont === f.key} onclick={() => appearance.set({ readingFont: f.key })}>{f.label}</button>
						{/each}
					</div>
				</div>
				<div class="set-row">
					<div class="sr-main">
						<div class="sr-t">Line spacing</div>
						<div class="sr-d">Extra spacing between lines of text.</div>
					</div>
					<div class="theme-seg">
						{#each SPACINGS as sp (sp.key)}
							<button type="button" aria-pressed={appearance.readingSpacing === sp.key} onclick={() => appearance.set({ readingSpacing: sp.key })}>{sp.label}</button>
						{/each}
					</div>
				</div>
				<div class="set-row">
					<div class="sr-main">
						<div class="sr-t">Line width</div>
						<div class="sr-d">Comfortable caps line length for easier reading on wide windows.</div>
					</div>
					<div class="theme-seg">
						{#each WIDTHS as w (w.key)}
							<button type="button" aria-pressed={appearance.readingWidth === w.key} onclick={() => appearance.set({ readingWidth: w.key })}>{w.label}</button>
						{/each}
					</div>
				</div>

				<div class="reading-preview">
					<div class="rp-label">Preview</div>
					<div class="jd-prose">
						<h3>Senior Software Engineer</h3>
						<p>
							We are looking for an experienced engineer to help design and build the next
							generation of our platform. You will work across the stack, shipping features that
							thousands of users rely on every day.
						</p>
						<ul>
							<li>Design, build, and maintain services and user-facing features.</li>
							<li>Collaborate with product and design on scope and trade-offs.</li>
						</ul>
					</div>
				</div>
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>Company blacklist</h2></div>
			<div class="card-b">
				<p class="muted" style="margin-bottom:14px">
					Jobs from these companies are dropped during ingest, before they ever reach your queue.
					Matching ignores casing, punctuation, and legal suffixes, so <em>Meta</em>, <em>Meta Inc</em>,
					and <em>Meta, Inc.</em> all count as the same company. Editing the list only affects future
					ingests, not jobs already saved.
				</p>

				<form method="POST" action="?/addBlacklist" class="bl-add" use:enhance>
					<input
						class="input"
						type="text"
						name="company"
						placeholder="Company name"
						autocomplete="off"
						required
					/>
					<input
						class="input"
						type="text"
						name="reason"
						placeholder="Reason (optional)"
						autocomplete="off"
					/>
					<button type="submit" class="btn primary">Add</button>
				</form>

				{#if form && 'blacklistError' in form && form.blacklistError}
					<p class="err-text" style="margin-top:10px">{form.blacklistError}</p>
				{/if}
				{#if form?.blacklistOk && 'blacklistMessage' in form && form.blacklistMessage}
					<p class="banner ok" style="margin-top:10px">{form.blacklistMessage}</p>
				{/if}

				{#if data.blacklist.length === 0}
					<p class="muted bl-empty">No companies blacklisted yet.</p>
				{:else}
					<ul class="bl-list">
						{#each data.blacklist as c (c.id)}
							<li>
								<div class="bl-main">
									<span class="bl-name">{c.name}</span>
									{#if c.reason}<span class="bl-reason">{c.reason}</span>{/if}
								</div>
								<form method="POST" action="?/removeBlacklist" use:enhance>
									<input type="hidden" name="id" value={c.id} />
									<button type="submit" class="btn ghost sm bl-remove" aria-label="Remove {c.name}"
										>Remove</button
									>
								</form>
							</li>
						{/each}
					</ul>
				{/if}
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

						{#if chosen}
							<div class="field scoring-field">
								<span>Scoring model <span class="opt">baseline / bulk</span></span>
								<input
									class="input"
									type="text"
									name="scoring_model"
									value={ai.scoring_model ?? ''}
									placeholder={ai.scoring_model_default ?? 'provider default'}
								/>
								<small class="fieldhelp">
									Scoring a whole ingest is many calls at once, so it uses a lighter, cheaper model
									than drafting to spare your usage limits. Tailored re-scores still use your main
									model. Leave blank for the default{ai.scoring_model_default
										? ` (${ai.scoring_model_default})`
										: ''}.
								</small>
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
	.accent-swatches {
		display: inline-flex;
		gap: 9px;
	}
	.sw {
		width: 24px;
		height: 24px;
		border-radius: 50%;
		background: var(--sw);
		border: 2px solid var(--surface);
		box-shadow: 0 0 0 1px var(--border-2);
		padding: 0;
	}
	.sw:hover {
		box-shadow: 0 0 0 1px var(--faint);
	}
	.sw.on {
		box-shadow: 0 0 0 2px var(--fg);
	}
	.reading-preview {
		margin-top: 16px;
		padding: 14px 16px;
		border: 1px solid var(--border);
		border-radius: 10px;
		background: var(--bg);
	}
	.rp-label {
		font-size: 10.5px;
		font-weight: 640;
		letter-spacing: 0.09em;
		text-transform: uppercase;
		color: var(--faint);
		margin-bottom: 10px;
	}
	.reading-preview .jd-prose :last-child {
		margin-bottom: 0;
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
	.scoring-field {
		max-width: 340px;
		margin-bottom: 14px;
	}
	.scoring-field > span {
		display: block;
		font-size: 13px;
		font-weight: 600;
		margin-bottom: 5px;
	}
	.scoring-field .opt {
		font-weight: 500;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
		margin-left: 4px;
	}
	.fieldhelp {
		display: block;
		margin-top: 6px;
		font-size: 12px;
		line-height: 1.5;
		color: var(--faint);
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
	.bl-add {
		display: flex;
		gap: 8px;
		align-items: center;
		flex-wrap: wrap;
	}
	.bl-add .input {
		flex: 1;
		min-width: 140px;
	}
	.bl-empty {
		margin-top: 14px;
	}
	.bl-list {
		list-style: none;
		padding: 0;
		margin: 14px 0 0;
	}
	.bl-list li {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 10px 0;
		border-bottom: 1px solid var(--border);
	}
	.bl-list li:last-child {
		border-bottom: 0;
		padding-bottom: 0;
	}
	.bl-main {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.bl-name {
		font-weight: 600;
		font-size: 13px;
		word-break: break-word;
	}
	.bl-reason {
		font-size: 12px;
		color: var(--faint);
		word-break: break-word;
	}
	.bl-remove {
		flex-shrink: 0;
	}
</style>
