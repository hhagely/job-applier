<script lang="ts">
	// Shared tailored-draft generator, used by both the Queue detail pane (/) and
	// the job detail page (/jobs/[id]) so the two can't drift. Renders the *body*
	// of the "Tailored draft" card; the parent owns the surrounding card shell.
	//
	// Both host pages define `?/generateDraft` + `?/renderDraft` form actions and
	// both read the target from a hidden `job_id` field (the /jobs/[id] actions
	// read the route param and ignore the body, so emitting job_id is harmless
	// there). Refresh-after-change is delegated to `onDraftChange` because each
	// host owns its draft state differently — the Queue lazy-fetches it per
	// selection, /jobs/[id] gets it from the loader.
	import { enhance } from '$app/forms';
	import { api, type Draft } from '$lib/api';
	import { createTaskRunner } from '$lib/taskRunner.svelte';
	import { fmtDateTime } from '$lib/date';
	import ScoreProgress from '$lib/ScoreProgress.svelte';

	let {
		jobId,
		draft,
		hasProvider,
		apiBase,
		onDraftChange
	}: {
		jobId: number;
		draft: Draft | null | undefined;
		hasProvider: boolean;
		apiBase: string;
		/** Called after a draft is (re)generated or re-rendered, so the host can
		 * refresh whichever draft state it owns. */
		onDraftChange?: () => void | Promise<void>;
	} = $props();

	// Tracked by kind + this job's id (`ref`) so a draft running for another job
	// doesn't light up this card. Progress flows over the shared event stream.
	const draftRun = createTaskRunner({
		kind: 'draft',
		ref: () => String(jobId),
		onSettled: () => onDraftChange?.(),
		failMessage: 'could not start drafting'
	});
</script>

<div style="margin-bottom:12px">
	{#if !hasProvider}
		<a class="btn" href="/settings" title="Select an AI CLI in Settings">
			Generate tailored draft — set up AI
		</a>
	{:else}
		<form method="POST" action="?/generateDraft" use:enhance={draftRun.enhance}>
			<input type="hidden" name="job_id" value={jobId} />
			<button type="submit" class="btn primary" disabled={draftRun.busy}>
				{#if draftRun.busy}
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

{#if draftRun.error && !draftRun.snap}
	<p class="err-text" style="margin-bottom:12px">{draftRun.error}</p>
{/if}
<ScoreProgress
	task={draftRun.snap}
	onDismiss={draftRun.dismiss}
	runningVerb="Generating"
	doneVerb="Generated"
	resultsLabel="stages"
/>

{#if draft && (draft.has_resume_pdf || draft.has_cover_letter_pdf)}
	<div class="draft-actions">
		{#if draft.has_resume_pdf}
			<a class="btn primary" href={api.draftResumePdfUrl(apiBase, jobId)} download>Download resume PDF</a>
		{:else}<span class="muted">No tailored resume yet</span>{/if}
		{#if draft.has_cover_letter_pdf}
			<a class="btn primary" href={api.draftCoverLetterPdfUrl(apiBase, jobId)} download>Download cover letter PDF</a>
		{:else}<span class="muted">No cover letter yet</span>{/if}
	</div>
	<div class="draft-meta">
		{#if draft.updated_at}<span class="muted small">Last generated {fmtDateTime(draft.updated_at)}</span>{/if}
		<form
			method="POST"
			action="?/renderDraft"
			style="display:inline"
			use:enhance={() =>
				async ({ result }) => {
					if (result.type === 'success') await onDraftChange?.();
					else if (result.type === 'failure')
						draftRun.setError((result.data?.error as string) ?? 'could not re-render');
				}}
		>
			<input type="hidden" name="job_id" value={jobId} />
			<button type="submit" class="btn sm">Re-render PDFs from markdown</button>
		</form>
	</div>
	<p class="muted small" style="margin-top:8px">
		Use Regenerate tailored draft above to rebuild the markdown from the current job description.
	</p>
{:else}
	<p class="muted small">
		No draft yet. Use the button above to generate a tailored resume and cover letter (both PDFs). Drafts strictly use
		only what's in your master resume — they reorder and re-emphasize, but won't invent skills or experience.
	</p>
{/if}

<style>
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
</style>
