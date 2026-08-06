<script lang="ts">
	import { enhance } from '$app/forms';
	import { api } from '$lib/api';
	import Icon from '$lib/Icon.svelte';
	import ScoreProgress from '$lib/ScoreProgress.svelte';
	import { createTaskRunner } from '$lib/taskRunner.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let apiBase = $derived(data.apiBase ?? '');
	let resume = $derived(form?.resume ?? data.resume);
	let uploading = $state(false);
	// Only the upload action sets `staleCount`, so answering the prompt (either
	// button) replaces `form` and retires it.
	let staleCount = $derived(form?.staleCount ?? 0);
	let keptCount = $derived(form?.kept ?? 0);

	const rescore = createTaskRunner({
		kind: 'score_pending',
		failMessage: 'could not start scoring'
	});

	let fileInput = $state<HTMLInputElement | null>(null);
</script>

<div class="view-head">
	<div class="vh-titles">
		<h1>Resume</h1>
		<div class="vh-sub">The PDF used to score every job. Newest upload wins.</div>
	</div>
	<div class="vh-actions">
		<button type="button" class="btn primary" onclick={() => fileInput?.click()}>
			<Icon name="upload" size={15} stroke={2} /> Upload new
		</button>
	</div>
</div>

<div class="view-body">
	<div class="stack">
		<ScoreProgress task={rescore.snap} onDismiss={rescore.dismiss} />
		<div class="card">
			<div class="card-h"><h3>Upload</h3></div>
			<div class="card-b">
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
					<div class="upload-drop">
						<div class="ud-ico"><Icon name="upload" size={20} /></div>
						<div style="flex:1">
							<div style="font-weight:600;font-size:13px">Drop your resume PDF here</div>
							<div class="hint">
								The app extracts plain text and stores it as the active resume. Older versions are
								kept but inactive.
							</div>
						</div>
						<input
							bind:this={fileInput}
							type="file"
							name="file"
							accept="application/pdf,.pdf"
							required
							onchange={(e) => (e.currentTarget.files?.length ? e.currentTarget.form?.requestSubmit() : null)}
							style="display:none"
						/>
						<button type="button" class="btn" onclick={() => fileInput?.click()}>Choose file</button>
						<button type="submit" class="btn primary" disabled={uploading}>{uploading ? 'Uploading…' : 'Upload'}</button>
					</div>
				</form>

				{#if form?.error}<p class="err-text" style="margin-top:12px">{form.error}</p>{/if}
				{#if rescore.error && !rescore.snap}
					<p class="err-text" style="margin-top:12px">{rescore.error}</p>
				{/if}

				<!-- Every upload writes a new resume row, which invalidates every score at
				     once. Ask here, while the user still knows how big the edit was. -->
				{#if staleCount > 0}
					<div class="banner warn stale-choice" style="margin-top:12px">
						<div>
							<strong>{staleCount} scored {staleCount === 1 ? 'job was' : 'jobs were'}</strong> matched
							against your previous resume. Keep those scores, or re-run them against this one?
						</div>
						<div class="sc-actions">
							<form method="POST" action="?/keepScores" use:enhance>
								<button type="submit" class="btn">Keep existing scores</button>
							</form>
							<form method="POST" action="?/rescoreStale" use:enhance={rescore.enhance}>
								<button type="submit" class="btn primary" disabled={rescore.busy}>
									{rescore.busy ? 'Scoring…' : `Re-score ${staleCount}`}
								</button>
							</form>
						</div>
						<div class="hint">
							Typo or reworded bullet? Keep them — re-scoring spends an AI call per job. Rewrote a
							section, or changed your titles or tech? Re-score.
						</div>
					</div>
				{/if}
				{#if keptCount > 0}
					<p class="banner info" style="margin-top:12px">
						Kept {keptCount} existing {keptCount === 1 ? 'score' : 'scores'} — they now count against the
						new resume.
					</p>
				{/if}
				<!-- Where to go after an upload. Which jobs even reach the scorer is
				     decided by the search profile, so that's the next stop — and with a
				     provider detected, it can be filled in from the resume just uploaded. -->
				{#if form?.nextSteps}
					<div class="banner info next-step" style="margin-top:12px">
						<div>
							<strong>Resume saved. Next: your search profile.</strong>
							It's the filter that decides which jobs get ingested at all — role titles, seniority,
							and required / excluded tech.
							{#if form.aiCanSuggest}
								Head there and hit <em>Suggest roles from resume</em> to have your AI provider read this
								resume and propose criteria — nothing changes until you accept them.
							{:else}
								Set the criteria there by hand, or <a href="/settings">set up an AI provider</a> to have
								them suggested from this resume.
							{/if}
						</div>
						<a class="btn primary" href="/search">Open search profile</a>
					</div>
				{/if}
			</div>
		</div>

		{#if resume}
			<div class="card">
				<div class="card-h"><h3>Active resume</h3><span class="tag status-applied" style="margin-left:auto">active</span></div>
				<div class="card-b">
					<div class="meta-table">
						<div class="d-meta-row"><span class="dm-k">Filename</span><span class="dm-v mono">{resume.original_filename}</span></div>
						<div class="d-meta-row"><span class="dm-k">Pages</span><span class="dm-v mono">{resume.page_count ?? '—'}</span></div>
						<div class="d-meta-row"><span class="dm-k">Uploaded</span><span class="dm-v mono">{new Date(resume.uploaded_at).toLocaleString()}</span></div>
						<div class="d-meta-row">
							<span class="dm-k">Original PDF</span>
							<a class="d-link" href={api.resumePdfUrl(apiBase)} target="_blank" rel="noopener">Download ↓</a>
						</div>
					</div>
				</div>
			</div>

			<div class="card">
				<div class="card-h">
					<h3>Extracted text</h3>
					<span class="hint" style="margin-left:auto;margin-top:0">what the match scorer sees</span>
				</div>
				<div class="card-b">
					<p class="hint" style="margin-top:0;margin-bottom:12px">
						If this looks broken, your PDF is probably image-only — re-export as a text-based PDF.
					</p>
					<div class="extracted">{resume.extracted_text}</div>
				</div>
			</div>
		{:else}
			<div class="card"><div class="card-b"><p class="muted">No resume on file yet. Upload one above.</p></div></div>
		{/if}
	</div>
</div>

<style>
	.upload-drop {
		border: 1.5px dashed var(--border-2);
		border-radius: 10px;
		padding: 22px;
		display: flex;
		align-items: center;
		gap: 16px;
		background: var(--bg);
		flex-wrap: wrap;
	}
	.upload-drop .ud-ico {
		width: 40px;
		height: 40px;
		border-radius: 10px;
		background: var(--accent-soft);
		color: var(--accent);
		display: grid;
		place-items: center;
		flex: none;
	}
	.stale-choice {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 10px;
	}
	.next-step {
		display: flex;
		align-items: center;
		gap: 14px;
		flex-wrap: wrap;
	}
	.next-step > div {
		flex: 1;
		min-width: 260px;
		line-height: 1.55;
	}
	.next-step .btn {
		flex: none;
	}
	.sc-actions {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.stale-choice .hint {
		margin: 0;
	}
	.extracted {
		font-family: var(--mono);
		font-size: 11.5px;
		line-height: 1.65;
		color: var(--muted);
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 9px;
		padding: 14px 16px;
		max-height: 320px;
		overflow: auto;
		white-space: pre-wrap;
	}
</style>
