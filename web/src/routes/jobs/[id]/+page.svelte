<script lang="ts">
	import type { PageData } from './$types';
	import { api, type ApplicationStatus } from '$lib/api';

	let { data }: { data: PageData } = $props();

	const statuses: ApplicationStatus[] = [
		'new',
		'interested',
		'drafted',
		'applied',
		'rejected',
		'archived'
	];

	let job = $derived(data.job);
	let draft = $derived(data.draft);

	function fmtUpdated(iso: string | null | undefined): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleString();
	}

	const SOURCE_META: Record<string, { label: string; ease: 'easy' | 'med' | 'hard' }> = {
		greenhouse: { label: 'Greenhouse', ease: 'easy' },
		lever: { label: 'Lever', ease: 'easy' },
		ashby: { label: 'Ashby', ease: 'easy' },
		remoteok: { label: 'RemoteOK', ease: 'med' },
		weworkremotely: { label: 'WWR', ease: 'med' },
		workday: { label: 'Workday', ease: 'hard' },
		hackernews: { label: 'HN', ease: 'med' }
	};

	function sourceInfo(source: string): { label: string; ease: 'easy' | 'med' | 'hard' } {
		return SOURCE_META[source] ?? { label: source, ease: 'med' };
	}

	let si = $derived(sourceInfo(job.source));
</script>

<a href="/" class="back">← back to queue</a>

<header class="job-header">
	<h1>{job.title}</h1>
	<p class="company">
		<span class="source" data-ease={si.ease} title="Apply friction: {si.ease}">
			{si.label}
		</span>
		{job.company?.name ?? 'Unknown'}
		{#if job.location}· {job.location}{/if}
		{#if job.employment_type}· {job.employment_type}{/if}
	</p>
	<p>
		<a href={job.url} target="_blank" rel="noopener">View original posting →</a>
	</p>
</header>

<section class="grid">
	<div class="panel">
		<h2>Match score</h2>
		{#if job.score}
			<div class="score-big">{job.score.score}<span>/100</span></div>
			{#if job.score.reasoning}
				<p class="reasoning">{job.score.reasoning}</p>
			{/if}
			{#if job.score.rubric && Object.keys(job.score.rubric).length > 0}
				<details>
					<summary>Rubric breakdown</summary>
					<pre>{JSON.stringify(job.score.rubric, null, 2)}</pre>
				</details>
			{/if}
		{:else}
			<p class="muted">
				Not yet scored. Run <code>/match-pending</code> in Claude Code to score the queue.
			</p>
		{/if}
	</div>

	<div class="panel">
		<h2>Status</h2>
		<form method="POST" action="?/setStatus" class="status-form">
			<select name="status" value={job.application?.status ?? 'new'}>
				{#each statuses as s}
					<option value={s}>{s}</option>
				{/each}
			</select>
			<button type="submit">Update</button>
		</form>

		<h3>Notes</h3>
		<form method="POST" action="?/setNotes">
			<textarea name="notes" rows="4" placeholder="Personal notes about this role…"
				>{job.application?.notes ?? ''}</textarea>
			<button type="submit">Save notes</button>
		</form>
	</div>
</section>

<section class="panel draft">
	<h2>Tailored draft</h2>
	{#if draft && (draft.has_resume_pdf || draft.has_cover_letter_pdf)}
		<div class="draft-actions">
			{#if draft.has_resume_pdf}
				<a class="btn primary" href={api.draftResumePdfUrl(job.id)} download>
					Download resume PDF
				</a>
			{:else}
				<span class="muted">No tailored resume yet</span>
			{/if}
			{#if draft.has_cover_letter_pdf}
				<a class="btn primary" href={api.draftCoverLetterPdfUrl(job.id)} download>
					Download cover letter PDF
				</a>
			{:else}
				<span class="muted">No cover letter yet</span>
			{/if}
		</div>
		<div class="draft-meta">
			{#if draft.updated_at}
				<span class="muted">Last generated {fmtUpdated(draft.updated_at)}</span>
			{/if}
			<form method="POST" action="?/renderDraft" class="inline-form">
				<button type="submit" class="btn">Re-render PDFs from markdown</button>
			</form>
		</div>
		<p class="muted hint">
			Run <code>/draft {job.id}</code> in Claude Code to regenerate the tailored markdown from the
			current job description.
		</p>
	{:else}
		<p class="muted">
			No draft yet. Run <code>/draft {job.id}</code> in Claude Code to generate a tailored resume and
			cover letter (both PDFs). Drafts strictly use only what's in your master resume — they reorder
			and re-emphasize, but won't invent skills or experience.
		</p>
	{/if}
</section>

<section class="panel description">
	<h2>Description</h2>
	{@html job.description}
</section>

<style>
	.back {
		display: inline-block;
		margin-bottom: 1rem;
		font-size: 0.9rem;
	}
	.job-header h1 {
		margin: 0 0 0.25rem;
		font-size: 1.5rem;
	}
	.company {
		color: var(--muted);
		margin: 0 0 0.5rem;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}
	.source {
		font-size: 0.7rem;
		letter-spacing: 0.02em;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: #20262d;
		color: var(--muted);
	}
	.source[data-ease='easy'] {
		background: rgba(46, 160, 67, 0.18);
		color: var(--ok);
	}
	.source[data-ease='med'] {
		background: rgba(210, 153, 34, 0.18);
		color: var(--warn);
	}
	.source[data-ease='hard'] {
		background: rgba(248, 81, 73, 0.16);
		color: var(--bad);
	}
	.grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
		margin: 1.5rem 0;
	}
	@media (max-width: 720px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}
	.panel {
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
		padding: 1rem 1.25rem;
	}
	.panel h2 {
		font-size: 1rem;
		margin: 0 0 0.75rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.panel h3 {
		font-size: 0.85rem;
		margin: 1rem 0 0.4rem;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.score-big {
		font-size: 2.5rem;
		font-weight: 700;
	}
	.score-big span {
		font-size: 1rem;
		color: var(--muted);
		font-weight: 400;
	}
	.reasoning {
		color: var(--fg);
		margin-top: 0.5rem;
	}
	.muted {
		color: var(--muted);
	}
	.status-form {
		display: flex;
		gap: 0.5rem;
	}
	select,
	textarea,
	button {
		font: inherit;
		color: var(--fg);
		background: var(--bg);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.4rem 0.6rem;
	}
	textarea {
		width: 100%;
		box-sizing: border-box;
		resize: vertical;
	}
	button {
		cursor: pointer;
		background: var(--panel-border);
	}
	button:hover {
		border-color: var(--accent);
	}
	pre {
		background: var(--bg);
		padding: 0.75rem;
		border-radius: 6px;
		overflow-x: auto;
		font-size: 0.85rem;
	}
	.description :global(*) {
		color: var(--fg) !important;
		background-color: transparent !important;
	}
	.description :global(a) {
		color: var(--accent) !important;
	}
	.description :global(p) {
		line-height: 1.5;
	}
	.description :global(ul),
	.description :global(ol) {
		padding-left: 1.5rem;
	}
	.description :global(img) {
		max-width: 100%;
		height: auto;
	}
	.description :global(pre),
	.description :global(code) {
		background: var(--bg) !important;
	}
	.draft {
		margin-bottom: 1rem;
	}
	.draft-actions {
		display: flex;
		gap: 0.6rem;
		flex-wrap: wrap;
		margin-bottom: 0.6rem;
	}
	.draft-meta {
		display: flex;
		align-items: center;
		gap: 0.85rem;
		flex-wrap: wrap;
		font-size: 0.85rem;
	}
	.btn {
		display: inline-block;
		padding: 0.4rem 0.75rem;
		border-radius: 6px;
		border: 1px solid var(--panel-border);
		background: var(--panel-border);
		color: var(--fg);
		font-size: 0.85rem;
		cursor: pointer;
	}
	.btn:hover {
		border-color: var(--accent);
		text-decoration: none;
	}
	.btn.primary {
		background: rgba(88, 166, 255, 0.18);
		color: var(--accent);
		border-color: var(--accent);
	}
	.inline-form {
		display: inline;
	}
	.hint {
		margin-top: 0.6rem;
		font-size: 0.8rem;
	}
</style>
