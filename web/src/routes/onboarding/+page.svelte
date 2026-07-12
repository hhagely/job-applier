<script lang="ts">
	import { untrack } from 'svelte';
	import { api, getApiBase, type Provider } from '$lib/api';
	import ProviderList from '$lib/ProviderList.svelte';
	import { taskStream } from '$lib/taskStream.svelte';
	import { US_STATES } from '$lib/usStates';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Browser-side base (window.__API_BASE__), which can differ from the server base
	// in the packaged app. All wizard mutations are client-side fetches.
	const base = () => getApiBase();

	const STEPS = ['AI provider', 'Resume', 'Fetch jobs'] as const;
	let step = $state(1);

	// --- Step 1: AI provider --------------------------------------------------
	// Seed mutable wizard state from the initial load (untrack: initial value only,
	// matching the search page's convention).
	let providers = $state<Provider[]>(untrack(() => data.providers?.providers ?? []));
	let anyAvailable = $derived(providers.some((p) => p.available));
	let chosen = $state(
		untrack(
			() =>
				data.providers?.selected ?? data.providers?.providers?.find((p) => p.available)?.name ?? ''
		)
	);
	let model = $state(untrack(() => data.providers?.model ?? ''));
	let savingProvider = $state(false);
	let providerErr = $state('');

	async function saveProvider() {
		if (!chosen) return;
		savingProvider = true;
		providerErr = '';
		try {
			await api.selectProvider(fetch, base(), chosen, chosen === 'ollama' ? model : undefined);
			step = 2;
		} catch (e) {
			providerErr = e instanceof Error ? e.message : String(e);
		} finally {
			savingProvider = false;
		}
	}

	// --- Step 2: Resume -------------------------------------------------------
	let resume = $state(untrack(() => data.resume));
	let uploading = $state(false);
	let resumeErr = $state('');
	let fileInput = $state<HTMLInputElement | null>(null);

	async function uploadResume(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		uploading = true;
		resumeErr = '';
		try {
			resume = await api.uploadResume(fetch, base(), file);
		} catch (err) {
			resumeErr = err instanceof Error ? err.message : String(err);
		} finally {
			uploading = false;
		}
	}

	// --- Step 3: Fetch jobs ---------------------------------------------------
	// Optional home state — set before the first ingest so state-restricted
	// postings ("we can only hire in X, Y, Z") that exclude it are filtered out.
	// Autosaved on change; merged into the existing profile so nothing is clobbered.
	let home_state = $state(untrack(() => data.searchProfile?.home_state ?? ''));
	let stateSaving = $state(false);
	let stateSaved = $state(false);
	let stateErr = $state('');

	async function saveHomeState() {
		stateSaving = true;
		stateSaved = false;
		stateErr = '';
		try {
			const cur = await api.getSearchProfile(fetch, base());
			await api.saveSearchProfile(fetch, base(), {
				role_titles: cur.role_titles,
				seniority_terms: cur.seniority_terms,
				required_tech: cur.required_tech,
				excluded_tech: cur.excluded_tech,
				extracted_skills: cur.extracted_skills,
				home_state: home_state || null
			});
			stateSaved = true;
		} catch (e) {
			stateErr = e instanceof Error ? e.message : String(e);
		} finally {
			stateSaving = false;
		}
	}

	let ingestState = $state<'idle' | 'running' | 'done' | 'error'>('idle');
	let ingestProgress = $state({ done: 0, total: 0 });
	let ingestCount = $state(0);
	let ingestErr = $state('');
	let ingestTaskId = $state<string | null>(null);

	async function fetchJobs() {
		ingestState = 'running';
		ingestErr = '';
		try {
			const { task_id } = await api.startIngest(fetch, base());
			// Event-driven: hand the id to the shared stream and react below. The
			// root layout already holds one EventSource for all task progress, so
			// there's no poll loop here.
			ingestTaskId = task_id;
		} catch (e) {
			ingestErr = e instanceof Error ? e.message : String(e);
			ingestState = 'error';
		}
	}

	// Drive the wizard's ingest UI off the shared event stream, keyed by the id we
	// started, so per-source progress and the terminal state arrive as pushes.
	$effect(() => {
		if (!ingestTaskId) return;
		const snap = taskStream.tasks[ingestTaskId];
		if (!snap) return;
		ingestProgress = { done: snap.done, total: snap.total };
		if (snap.status === 'done') {
			// results are per-source summary strings; count them loosely.
			ingestCount = snap.results.length;
			ingestState = 'done';
		} else if (snap.status === 'error') {
			ingestErr = snap.errors.join('; ') || 'Ingest failed.';
			ingestState = 'error';
		}
	});
</script>

<div class="ob-wrap">
	<div class="ob-card card">
		<div class="ob-head">
			<h1>Welcome to job-applier</h1>
			<p class="muted">
				Three quick steps to get scoring, drafting, and your first batch of jobs. You can skip any of
				them — the app works without AI, and you can always finish setup later.
			</p>
		</div>

		<ol class="steps">
			{#each STEPS as label, i (label)}
				<li class:active={step === i + 1} class:done={step > i + 1}>
					<span class="s-num">{step > i + 1 ? '✓' : i + 1}</span>
					<span class="s-label">{label}</span>
				</li>
			{/each}
		</ol>

		<div class="ob-body">
			{#if step === 1}
				<p class="muted step-lead">
					Scoring and drafting run through an AI CLI you already have installed, on your own
					subscription. The app never calls a vendor API or handles keys.
				</p>

				{#if !anyAvailable}
					<ProviderList {providers} {anyAvailable} bind:chosen />
					<div class="ob-actions">
						<button type="button" class="btn primary" onclick={() => (step = 2)}>Continue without AI</button>
					</div>
				{:else}
					<ProviderList {providers} {anyAvailable} bind:chosen />

					{#if chosen === 'ollama'}
						<div class="field" style="max-width:280px;margin-bottom:12px">
							<span>Model</span>
							<input class="input" type="text" bind:value={model} placeholder="llama3.1" />
						</div>
					{/if}

					{#if providerErr}<p class="err-text">{providerErr}</p>{/if}

					<div class="ob-actions">
						<button type="button" class="btn" onclick={() => (step = 2)}>Skip this step</button>
						<button type="button" class="btn primary" disabled={!chosen || savingProvider} onclick={saveProvider}>
							{savingProvider ? 'Saving…' : 'Save & continue'}
						</button>
					</div>
				{/if}
			{:else if step === 2}
				<p class="muted step-lead">
					Upload your resume PDF. It's the document every job is scored against and the basis for
					tailored drafts. It stays local — nothing is uploaded to a server.
				</p>

				{#if resume}
					<div class="banner info">
						Active resume: <strong>{resume.original_filename}</strong>
						{#if resume.page_count}({resume.page_count} pages){/if}
					</div>
				{/if}

				<div class="upload-drop">
					<div style="flex:1">
						<div style="font-weight:600;font-size:13px">
							{resume ? 'Replace resume PDF' : 'Choose your resume PDF'}
						</div>
						<div class="hint">Text-based PDF, please — image-only scans extract poorly.</div>
					</div>
					<input
						bind:this={fileInput}
						type="file"
						accept="application/pdf,.pdf"
						onchange={uploadResume}
						style="display:none"
					/>
					<button type="button" class="btn" onclick={() => fileInput?.click()} disabled={uploading}>
						{uploading ? 'Uploading…' : 'Choose file'}
					</button>
				</div>

				{#if resumeErr}<p class="err-text" style="margin-top:10px">{resumeErr}</p>{/if}

				<div class="ob-actions">
					<button type="button" class="btn" onclick={() => (step = 1)}>Back</button>
					<button type="button" class="btn" onclick={() => (step = 3)}>Skip this step</button>
					<button type="button" class="btn primary" disabled={!resume} onclick={() => (step = 3)}>Continue</button>
				</div>
			{:else}
				<p class="muted step-lead">
					Pull jobs from every configured source. They're filtered on the way in; only roles that
					pass your criteria are saved. This needs no AI provider — just a network connection.
				</p>

				<div class="field state-field">
					<span>State of residence <span class="opt">(optional)</span></span>
					<select
						class="input state-select"
						aria-label="State of residence"
						bind:value={home_state}
						onchange={saveHomeState}
					>
						<option value="">— Not set (don't filter by state) —</option>
						{#each US_STATES as st (st)}
							<option value={st}>{st}</option>
						{/each}
					</select>
					<small class="state-hint">
						Some employers can only hire in certain states. Set yours and ingest drops postings
						whose "we can only hire in X, Y, Z" list leaves your state out. Used <strong>only</strong>
						for this filter, stored locally, never sent anywhere. You can change it anytime on the
						Search page.
						{#if stateSaving}<span class="muted"> · Saving…</span>{:else if stateSaved}<span class="saved"> · Saved</span>{/if}
					</small>
					{#if stateErr}<span class="err-text">{stateErr}</span>{/if}
				</div>

				{#if ingestState === 'idle'}
					<div class="ob-actions">
						<button type="button" class="btn primary" onclick={fetchJobs}>Fetch jobs now</button>
					</div>
				{:else if ingestState === 'running'}
					<div class="banner info">
						Fetching… {ingestProgress.done}/{ingestProgress.total} sources
					</div>
				{:else if ingestState === 'done'}
					<div class="banner info">
						Done — pulled from {ingestCount} {ingestCount === 1 ? 'source' : 'sources'}. Your queue is ready.
					</div>
				{:else}
					<p class="err-text">Ingest failed: {ingestErr}</p>
					<div class="ob-actions">
						<button type="button" class="btn" onclick={fetchJobs}>Retry</button>
					</div>
				{/if}

				<form method="POST" action="?/dismiss" class="ob-actions">
					<button type="submit" class="btn primary">
						{ingestState === 'done' ? 'Go to dashboard' : 'Finish'}
					</button>
				</form>
			{/if}
		</div>

		<div class="ob-foot">
			<form method="POST" action="?/dismiss">
				<button type="submit" class="ob-skip">Skip setup for now</button>
			</form>
		</div>
	</div>
</div>

<style>
	.ob-wrap {
		display: grid;
		place-items: start center;
		padding: 40px 20px;
		overflow: auto;
		height: 100%;
	}
	.ob-card {
		width: 100%;
		max-width: 620px;
		padding: 28px 30px 20px;
	}
	.ob-head h1 {
		font-size: 22px;
		margin: 0 0 6px;
	}
	.ob-head .muted {
		font-size: 13px;
		line-height: 1.6;
		margin: 0;
	}
	.steps {
		display: flex;
		gap: 8px;
		list-style: none;
		padding: 0;
		margin: 22px 0;
	}
	.steps li {
		display: flex;
		align-items: center;
		gap: 7px;
		flex: 1;
		font-size: 12px;
		color: var(--faint);
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 8px;
	}
	.steps li.active {
		color: var(--accent);
		border-color: var(--accent);
		background: var(--accent-soft);
		font-weight: 600;
	}
	.steps li.done {
		color: var(--strong);
	}
	.s-num {
		display: grid;
		place-items: center;
		width: 20px;
		height: 20px;
		border-radius: 50%;
		background: var(--border);
		color: var(--fg);
		font-size: 11px;
		font-weight: 600;
		flex: none;
	}
	.steps li.active .s-num {
		background: var(--accent);
		color: var(--accent-fg);
	}
	.step-lead {
		font-size: 13px;
		line-height: 1.6;
		margin: 0 0 16px;
	}
	.ob-body {
		min-height: 160px;
	}
	.ob-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 18px;
	}
	.ob-foot {
		margin-top: 18px;
		padding-top: 14px;
		border-top: 1px solid var(--border);
		display: flex;
		justify-content: center;
	}
	.ob-skip {
		font-size: 12px;
		color: var(--faint);
	}
	.ob-skip:hover {
		color: var(--fg);
		text-decoration: underline;
	}
	.upload-drop {
		border: 1.5px dashed var(--border-2);
		border-radius: 10px;
		padding: 20px;
		display: flex;
		align-items: center;
		gap: 16px;
		background: var(--bg);
		flex-wrap: wrap;
	}
	.state-field {
		margin-bottom: 18px;
	}
	.state-select {
		max-width: 320px;
	}
	.state-hint {
		line-height: 1.5;
		max-width: 60ch;
	}
	.state-field .opt {
		color: var(--faint);
		font-weight: 500;
	}
	.saved {
		color: var(--strong);
		font-weight: 600;
	}
</style>
