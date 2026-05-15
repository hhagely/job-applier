<script lang="ts">
	import { enhance } from '$app/forms';
	import { api } from '$lib/api';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let resume = $derived(form?.resume ?? data.resume);
	let uploading = $state(false);
	let staleCount = $derived(form?.ok ? (form.staleCount ?? 0) : 0);
</script>

<h1>Resume</h1>

<section class="panel">
	<h2>Upload</h2>
	<p class="muted">
		Upload the PDF you actually send to employers. The API extracts plain text and
		stores it as the active resume. The most recent upload wins; older versions are kept
		but inactive.
	</p>

	<form
		method="POST"
		action="?/upload"
		enctype="multipart/form-data"
		use:enhance={() => {
			uploading = true;
			return async ({ update }) => {
				await update();
				uploading = false;
			};
		}}
	>
		<input type="file" name="file" accept="application/pdf,.pdf" required />
		<button type="submit" disabled={uploading}>{uploading ? 'Uploading…' : 'Upload'}</button>
	</form>

	{#if form?.error}
		<p class="error">{form.error}</p>
	{/if}

	{#if staleCount > 0}
		<p class="stale-banner">
			{staleCount} scored {staleCount === 1 ? 'job is' : 'jobs are'} now stale —
			run <code>/match-pending</code> in Claude Code to refresh.
		</p>
	{/if}
</section>

{#if resume}
	<section class="panel">
		<h2>Active resume</h2>
		<dl>
			<dt>Filename</dt>
			<dd>{resume.original_filename}</dd>
			<dt>Pages</dt>
			<dd>{resume.page_count ?? '—'}</dd>
			<dt>Uploaded</dt>
			<dd>{new Date(resume.uploaded_at).toLocaleString()}</dd>
			<dt>Original PDF</dt>
			<dd><a href={api.resumePdfUrl()} target="_blank" rel="noopener">Download</a></dd>
		</dl>

		<h3>Extracted text</h3>
		<p class="muted">
			This is what <code>/match-pending</code> sees when scoring jobs. If it looks broken,
			your PDF is probably image-only — re-export from the source as text-based PDF.
		</p>
		<pre>{resume.extracted_text}</pre>
	</section>
{:else}
	<section class="panel empty">
		<p>No resume on file yet. Upload one above.</p>
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
	.panel h3 {
		font-size: 0.85rem;
		margin: 1.25rem 0 0.4rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.muted {
		color: var(--muted);
		margin: 0 0 0.75rem;
	}
	.error {
		color: var(--bad);
		margin-top: 0.5rem;
	}
	.stale-banner {
		margin-top: 0.75rem;
		padding: 0.5rem 0.75rem;
		background: rgba(210, 153, 34, 0.15);
		border: 1px solid var(--warn);
		border-radius: 6px;
		color: var(--fg);
		font-size: 0.9rem;
	}
	.stale-banner code {
		background: var(--bg);
		padding: 0.05rem 0.3rem;
		border-radius: 3px;
	}
	form {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		flex-wrap: wrap;
	}
	input[type='file'] {
		color: var(--fg);
		flex: 1;
		min-width: 0;
	}
	button {
		font: inherit;
		color: var(--fg);
		background: var(--panel-border);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.4rem 0.9rem;
		cursor: pointer;
	}
	button:hover {
		border-color: var(--accent);
	}
	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		column-gap: 1rem;
		row-gap: 0.25rem;
		margin: 0;
	}
	dt {
		color: var(--muted);
		font-size: 0.85rem;
	}
	dd {
		margin: 0;
	}
	pre {
		background: var(--bg);
		padding: 0.75rem;
		border-radius: 6px;
		overflow-x: auto;
		font-size: 0.85rem;
		white-space: pre-wrap;
		max-height: 30rem;
		overflow-y: auto;
	}
	.empty {
		text-align: center;
		color: var(--muted);
	}
</style>
