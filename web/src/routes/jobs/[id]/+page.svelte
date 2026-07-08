<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { PageData } from './$types';
	import { api, type ApplicationStatus, type TaskSnapshot } from '$lib/api';
	import { draftCart } from '$lib/draftCart.svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import Icon from '$lib/Icon.svelte';
	import { scoreBandVar } from '$lib/score';
	import { sourceInfo } from '$lib/sources';

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

	let apiBase = $derived(data.apiBase ?? '');
	let job = $derived(data.job);
	let usedUnemp = $derived(job.application?.used_for_unemployment ?? false);
	let draft = $derived(data.draft);
	let scoreHistory = $derived(data.scoreHistory);
	let canonical = $derived(data.canonical);
	let si = $derived(sourceInfo(job.source));

	const hasProvider = $derived(Boolean(data.aiProvider));
	let draftTask = $state<TaskSnapshot | null>(null);
	let draftPolling = $state(false);
	let draftStarting = $state(false);
	let draftError = $state<string | null>(null);

	async function pollDraftTask(taskId: string) {
		draftPolling = true;
		const base = data.apiBase ?? '';
		try {
			// eslint-disable-next-line no-constant-condition
			while (true) {
				const snap = await api.getTask(fetch, base, taskId);
				draftTask = snap;
				if (snap.status !== 'running') break;
				await new Promise((r) => setTimeout(r, 1000));
			}
			await invalidateAll();
		} catch (e) {
			draftError = (e as Error).message;
		} finally {
			draftPolling = false;
		}
	}

	function dismissDraftPanel() {
		draftTask = null;
		draftError = null;
	}

	function fmtUpdated(iso: string | null | undefined): string {
		return iso ? new Date(iso).toLocaleString() : '';
	}

	let pendingStatus = $state<ApplicationStatus>('new');
	let followupInput = $state<string>('');
	function followupDefault(): string {
		const existing = job.application?.next_followup_at;
		if (existing) return new Date(existing).toISOString().slice(0, 10);
		return new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10);
	}
	$effect(() => {
		pendingStatus = job.application?.status ?? 'new';
		followupInput = followupDefault();
	});
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>{job.title}</h1>
		<div class="vh-sub d-org">
			<span class="pill src-{job.source}"><span class="dot-badge"></span>{si.label}</span>
			<b style="color:var(--fg)">{job.company?.name ?? 'Unknown'}</b>
			{#if job.location}· {job.location}{/if}
			{#if job.employment_type}· {job.employment_type}{/if}
		</div>
	</div>
	<div class="vh-actions">
		<a class="btn ghost" href="/">← Queue</a>
		<button
			type="button"
			class="btn"
			class:primary={draftCart.has(job.id)}
			onclick={() => draftCart.toggle(job.id)}
			title="Queue this job for a /draft command"
		>
			{draftCart.has(job.id) ? '✓ In draft list' : '+ Add to draft list'}
		</button>
	</div>
</div>

<div class="view-body">
	<div class="stack" style="max-width:900px">
		{#if job.duplicate_of != null}
			<p class="banner warn">
				<strong>Duplicate</strong> of
				<a href={`/jobs/${job.duplicate_of}`}>
					#{job.duplicate_of}{#if canonical} — {canonical.title} at {canonical.company?.name ?? 'Unknown'}{/if}
				</a>
			</p>
		{/if}

		<a class="d-link" href={job.url} target="_blank" rel="noopener">
			View original posting <Icon name="external" size={12} stroke={2} />
		</a>

		<div class="grid-2">
			<div class="card">
				<div class="card-h">
					<h2>Match score</h2>
					{#if job.score}<span class="tag" style="margin-left:auto">{job.score.score_kind}</span>{/if}
				</div>
				<div class="card-b">
					{#if job.score}
						{#if job.score.is_stale}
							<p class="banner warn" style="margin-bottom:12px">Scored against an older resume — re-score to refresh.</p>
						{/if}
						<div class="match-hero">
							<span class="mh-n" style="color:{scoreBandVar(job.score.score)}">{job.score.score}</span>
							<span class="mh-d">/100</span>
						</div>
						<p class="score-meta">
							<span>{fmtUpdated(job.score.scored_at)}</span>
							{#if job.score.resume_filename}· <span class="mono">{job.score.resume_filename}</span>{/if}
						</p>
						{#if job.score.reasoning}<div class="rationale">{job.score.reasoning}</div>{/if}
						{#if job.score.rubric && Object.keys(job.score.rubric).length > 0}
							<details class="det"><summary>Rubric breakdown</summary><pre>{JSON.stringify(job.score.rubric, null, 2)}</pre></details>
						{/if}
						{#if scoreHistory.length > 0}
							<details class="det"><summary>Previous scores ({scoreHistory.length})</summary>
								<ul class="history-list">
									{#each scoreHistory as h, i (i)}
										<li>
											<details>
												<summary>
													<span class="h-score">{h.score}/100</span>
													<span class="tag">{h.score_kind}</span>
													· <span>{fmtUpdated(h.scored_at)}</span>
													{#if h.resume_filename}· <span class="mono">{h.resume_filename}</span>{/if}
												</summary>
												{#if h.reasoning}<p class="rationale">{h.reasoning}</p>{/if}
												{#if h.rubric && Object.keys(h.rubric).length > 0}<pre>{JSON.stringify(h.rubric, null, 2)}</pre>{/if}
											</details>
										</li>
									{/each}
								</ul>
							</details>
						{/if}
					{:else}
						<p class="muted">Not yet scored. Run <code>/match-pending</code> or the Score-pending button on the queue.</p>
					{/if}
				</div>
			</div>

			<div class="card">
				<div class="card-h"><h2>Status &amp; tracking</h2></div>
				<div class="card-b">
					<form method="POST" action="?/setStatus" class="row-form">
						<select class="input" name="status" bind:value={pendingStatus} style="flex:1;min-width:9rem">
							{#each statuses as s (s)}<option value={s}>{s}</option>{/each}
						</select>
						{#if pendingStatus === 'applied'}
							<input class="input" type="date" name="next_followup_at" bind:value={followupInput} style="width:auto" />
						{/if}
						<button type="submit" class="btn">Update</button>
					</form>
					{#if job.application?.next_followup_at}
						<p class="muted small">
							Next follow-up: {new Date(job.application.next_followup_at).toLocaleDateString()}
							{#if job.application.last_contact_at}· last contact {new Date(job.application.last_contact_at).toLocaleDateString()}{/if}
							{#if job.application.outcome}· outcome: <strong>{job.application.outcome}</strong>{/if}
						</p>
					{/if}

					<div class="field-label">Unemployment claim</div>
					<form method="POST" action="?/setUnemployment" class="row-form">
						<input type="hidden" name="used" value={usedUnemp ? 'false' : 'true'} />
						<button type="submit" class="btn" class:primary={usedUnemp}>
							{usedUnemp ? '✓ Used for unemployment' : 'Mark used for unemployment'}
						</button>
						{#if usedUnemp && job.application?.used_for_unemployment_at}
							<span class="muted small">marked {new Date(job.application.used_for_unemployment_at).toLocaleDateString()}</span>
						{/if}
					</form>

					<div class="field-label">Notes</div>
					<form method="POST" action="?/setNotes">
						<textarea class="input" name="notes" rows="4" placeholder="Personal notes about this role…">{job.application?.notes ?? ''}</textarea>
						<button type="submit" class="btn sm" style="margin-top:8px">Save notes</button>
					</form>
				</div>
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>Tailored draft</h2></div>
			<div class="card-b">
				<div style="margin-bottom:12px">
					{#if !hasProvider}
						<a class="btn" href="/settings" title="Select an AI CLI in Settings">Generate tailored draft — set up AI</a>
					{:else}
						<form
							method="POST"
							action="?/generateDraft"
							use:enhance={() => {
								draftStarting = true;
								draftError = null;
								return async ({ result }) => {
									draftStarting = false;
									if (result.type === 'success' && result.data?.task_id) {
										draftTask = null;
										pollDraftTask(result.data.task_id as string);
									} else if (result.type === 'failure') {
										draftError = (result.data?.error as string) ?? 'could not start drafting';
									}
								};
							}}
						>
							<button type="submit" class="btn primary" disabled={draftStarting || draftPolling}>
								{#if draftStarting || draftPolling}
									Generating…
								{:else if draft && (draft.has_resume_md || draft.has_cover_letter_md)}
									Regenerate tailored draft
								{:else}
									Generate tailored draft
								{/if}
							</button>
						</form>
					{/if}
				</div>

				{#if draftError && !draftTask}<p class="err-text" style="margin-bottom:12px">{draftError}</p>{/if}
				{#if draftTask}
					<ScoreProgress task={draftTask} onDismiss={dismissDraftPanel} runningVerb="Generating" doneVerb="Generated" resultsLabel="stages" />
				{/if}

				{#if draft && (draft.has_resume_pdf || draft.has_cover_letter_pdf)}
					<div class="draft-actions">
						{#if draft.has_resume_pdf}
							<a class="btn primary" href={api.draftResumePdfUrl(apiBase, job.id)} download>Download resume PDF</a>
						{:else}<span class="muted">No tailored resume yet</span>{/if}
						{#if draft.has_cover_letter_pdf}
							<a class="btn primary" href={api.draftCoverLetterPdfUrl(apiBase, job.id)} download>Download cover letter PDF</a>
						{:else}<span class="muted">No cover letter yet</span>{/if}
					</div>
					<div class="draft-meta">
						{#if draft.updated_at}<span class="muted small">Last generated {fmtUpdated(draft.updated_at)}</span>{/if}
						<form method="POST" action="?/renderDraft" style="display:inline">
							<button type="submit" class="btn sm">Re-render PDFs from markdown</button>
						</form>
					</div>
					<p class="muted small" style="margin-top:8px">
						Run <code>/draft {job.id}</code> in Claude Code to regenerate the tailored markdown from the current job description.
					</p>
				{:else}
					<p class="muted small">
						No draft yet. Run <code>/draft {job.id}</code> in Claude Code (or the button above) to generate a tailored resume and cover
						letter (both PDFs). Drafts strictly use only what's in your master resume — they reorder and re-emphasize, but won't
						invent skills or experience.
					</p>
				{/if}
			</div>
		</div>

		<div class="card">
			<div class="card-h"><h2>Description</h2></div>
			<div class="card-b description">
				<!-- eslint-disable-next-line svelte/no-at-html-tags -->
				{@html job.description}
			</div>
		</div>
	</div>
</div>

<style>
	.d-org {
		display: flex;
		align-items: center;
		gap: 9px;
		flex-wrap: wrap;
	}
	.match-hero {
		display: flex;
		align-items: baseline;
		gap: 6px;
	}
	.match-hero .mh-n {
		font-family: var(--mono);
		font-size: 44px;
		font-weight: 680;
		letter-spacing: -0.03em;
		line-height: 1;
	}
	.match-hero .mh-d {
		font-family: var(--mono);
		color: var(--faint);
		font-size: 16px;
	}
	.score-meta {
		color: var(--faint);
		font-size: 12px;
		margin: 8px 0 0;
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.rationale {
		color: var(--muted);
		font-size: 13px;
		line-height: 1.6;
		margin-top: 12px;
	}
	.det {
		margin-top: 14px;
		border-top: 1px solid var(--border);
		padding-top: 12px;
	}
	.det summary {
		cursor: pointer;
		font-size: 12px;
		font-weight: 600;
		color: var(--accent);
	}
	.history-list {
		list-style: none;
		padding: 0;
		margin: 10px 0 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.history-list li {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 8px 10px;
	}
	.history-list summary {
		cursor: pointer;
		font-size: 12px;
		color: var(--muted);
		display: flex;
		gap: 6px;
		align-items: center;
		flex-wrap: wrap;
	}
	.h-score {
		font-weight: 600;
		color: var(--fg);
		font-variant-numeric: tabular-nums;
	}
	.row-form {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
	}
	.field-label {
		font-size: 12px;
		font-weight: 600;
		color: var(--fg);
		margin: 16px 0 8px;
	}
	.small {
		font-size: 11.5px;
	}
	.draft-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-bottom: 8px;
	}
	.draft-meta {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	pre {
		background: var(--bg);
		border: 1px solid var(--border);
		padding: 0.75rem;
		border-radius: 8px;
		overflow-x: auto;
		font-size: 12px;
		margin-top: 8px;
	}
	.description :global(*) {
		color: var(--fg) !important;
		background-color: transparent !important;
	}
	.description :global(a) {
		color: var(--accent) !important;
	}
	.description :global(p) {
		line-height: 1.6;
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
</style>
