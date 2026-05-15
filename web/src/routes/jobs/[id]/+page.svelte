<script lang="ts">
	import type { PageData } from './$types';
	import { api, type ApplicationStatus } from '$lib/api';

	let { data }: { data: PageData } = $props();

	const statuses: ApplicationStatus[] = [
		'new',
		'interested',
		'drafted',
		'applied',
		'screening',
		'interviewing',
		'rejected',
		'archived'
	];

	let job = $derived(data.job);
	let draft = $derived(data.draft);
	let scoreHistory = $derived(data.scoreHistory);
	let canonical = $derived(data.canonical);

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

	function defaultFollowupDate(): string {
		return new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10);
	}

	function followupDefault(): string {
		const existing = job.application?.next_followup_at;
		if (existing) return new Date(existing).toISOString().slice(0, 10);
		return defaultFollowupDate();
	}

	let pendingStatus = $state<ApplicationStatus>('new');
	let followupInput = $state<string>('');
	$effect(() => {
		pendingStatus = job.application?.status ?? 'new';
		followupInput = followupDefault();
	});
</script>

<a href="/" class="back">← back to queue</a>

{#if job.duplicate_of != null}
	<aside class="dup-banner">
		<strong>Duplicate</strong>
		of
		<a href={`/jobs/${job.duplicate_of}`}>
			#{job.duplicate_of}{#if canonical}
				— {canonical.title} at {canonical.company?.name ?? 'Unknown'}{/if}
		</a>
	</aside>
{/if}

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
			{#if job.score.is_stale}
				<p class="stale-note">
					Scored against an older resume — run <code>/match-pending</code> to refresh.
				</p>
			{/if}
			<div class="score-big">{job.score.score}<span>/100</span></div>
			<p class="score-meta">
				<span class="kind" data-kind={job.score.score_kind}>{job.score.score_kind}</span>
				<span class="dot">·</span>
				<span>{fmtUpdated(job.score.scored_at)}</span>
				{#if job.score.resume_filename}
					<span class="dot">·</span>
					<span class="resume">{job.score.resume_filename}</span>
				{/if}
			</p>
			{#if job.score.reasoning}
				<p class="reasoning">{job.score.reasoning}</p>
			{/if}
			{#if job.score.rubric && Object.keys(job.score.rubric).length > 0}
				<details>
					<summary>Rubric breakdown</summary>
					<pre>{JSON.stringify(job.score.rubric, null, 2)}</pre>
				</details>
			{/if}
			{#if scoreHistory.length > 0}
				<details class="history">
					<summary>Previous scores ({scoreHistory.length})</summary>
					<ul class="history-list">
						{#each scoreHistory as h, i (i)}
							<li>
								<details>
									<summary>
										<span class="h-score">{h.score}/100</span>
										<span class="kind" data-kind={h.score_kind}>{h.score_kind}</span>
										<span class="dot">·</span>
										<span>{fmtUpdated(h.scored_at)}</span>
										{#if h.resume_filename}
											<span class="dot">·</span>
											<span class="resume">{h.resume_filename}</span>
										{/if}
									</summary>
									{#if h.reasoning}
										<p class="reasoning">{h.reasoning}</p>
									{/if}
									{#if h.rubric && Object.keys(h.rubric).length > 0}
										<pre>{JSON.stringify(h.rubric, null, 2)}</pre>
									{/if}
								</details>
							</li>
						{/each}
					</ul>
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
			<select name="status" bind:value={pendingStatus}>
				{#each statuses as s}
					<option value={s}>{s}</option>
				{/each}
			</select>
			{#if pendingStatus === 'applied'}
				<input type="date" name="next_followup_at" bind:value={followupInput} />
			{/if}
			<button type="submit">Update</button>
		</form>
		{#if job.application?.next_followup_at}
			<p class="followup-meta">
				Next follow-up: {new Date(job.application.next_followup_at).toLocaleDateString()}
				{#if job.application.last_contact_at}
					· last contact {new Date(job.application.last_contact_at).toLocaleDateString()}
				{/if}
				{#if job.application.outcome}
					· outcome: <strong>{job.application.outcome}</strong>
				{/if}
			</p>
		{/if}

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
	.dup-banner {
		background: rgba(210, 153, 34, 0.15);
		border: 1px solid var(--warn);
		border-radius: 6px;
		padding: 0.5rem 0.85rem;
		margin-bottom: 1rem;
		font-size: 0.9rem;
		color: var(--fg);
	}
	.dup-banner strong {
		color: var(--warn);
		margin-right: 0.25rem;
	}
	.dup-banner a {
		color: var(--accent);
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
	.stale-note {
		margin: 0 0 0.6rem;
		padding: 0.4rem 0.6rem;
		background: rgba(210, 153, 34, 0.15);
		border: 1px solid var(--warn);
		border-radius: 6px;
		color: var(--fg);
		font-size: 0.85rem;
	}
	.stale-note code {
		background: var(--bg);
		padding: 0.05rem 0.3rem;
		border-radius: 3px;
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
	.score-meta {
		color: var(--muted);
		font-size: 0.8rem;
		margin: 0.2rem 0 0.5rem;
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}
	.score-meta .dot {
		color: var(--panel-border);
	}
	.kind {
		font-size: 0.7rem;
		letter-spacing: 0.02em;
		padding: 0.1rem 0.45rem;
		border-radius: 4px;
		background: #20262d;
		color: var(--muted);
		text-transform: lowercase;
	}
	.kind[data-kind='tailored'] {
		background: rgba(88, 166, 255, 0.18);
		color: var(--accent);
	}
	.kind[data-kind='baseline'] {
		background: rgba(210, 153, 34, 0.18);
		color: var(--warn);
	}
	.resume {
		font-family: ui-monospace, monospace;
	}
	.history {
		margin-top: 0.75rem;
	}
	.history > summary {
		color: var(--muted);
		font-size: 0.85rem;
		cursor: pointer;
	}
	.history-list {
		list-style: none;
		padding: 0;
		margin: 0.5rem 0 0;
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}
	.history-list li {
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.4rem 0.6rem;
	}
	.history-list summary {
		cursor: pointer;
		font-size: 0.85rem;
		color: var(--muted);
		display: flex;
		gap: 0.4rem;
		align-items: center;
		flex-wrap: wrap;
	}
	.h-score {
		font-weight: 600;
		color: var(--fg);
		font-variant-numeric: tabular-nums;
	}
	.muted {
		color: var(--muted);
	}
	.status-form {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
	}
	.followup-meta {
		margin: 0.5rem 0 0;
		color: var(--muted);
		font-size: 0.85rem;
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
