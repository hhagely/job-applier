<script lang="ts">
	// Body of the "Status & tracking" card: application status + follow-up date,
	// the unemployment-claim flag, and notes. Shared by the queue detail pane (/)
	// and /jobs/[id] so the two can't drift; the parent owns the card shell +
	// heading (like TailoredDraftCard). Both hosts define ?/setStatus,
	// ?/setUnemployment, ?/setNotes actions that read the target from the hidden
	// `job_id` field — the /jobs/[id] actions ignore it and use the route param.
	// Refresh-after-change is delegated to `onChange` (each host owns its data).
	import { enhance } from '$app/forms';
	import { APPLICATION_STATUSES, type Application, type ApplicationStatus } from '$lib/api';
	import { fmtDate, defaultFollowupDate } from '$lib/date';

	let {
		jobId,
		application,
		onChange
	}: {
		jobId: number;
		application: Application | null | undefined;
		/** Called after any mutation succeeds so the host can refresh its data. */
		onChange?: () => void | Promise<void>;
	} = $props();

	const usedUnemp = $derived(application?.used_for_unemployment ?? false);

	let pendingStatus = $state<ApplicationStatus>('new');
	let followupInput = $state<string>('');

	// Seed the editable fields from the application, but only when the *job*
	// changes — not on every data refresh, so an in-progress edit isn't clobbered
	// when a sibling form triggers a reload.
	let seededFor = -1;
	$effect(() => {
		if (jobId === seededFor) return;
		seededFor = jobId;
		pendingStatus = application?.status ?? 'new';
		const existing = application?.next_followup_at;
		followupInput = existing
			? new Date(existing).toISOString().slice(0, 10)
			: defaultFollowupDate();
	});

	// Refresh the host's list/loader on success without clearing inputs.
	function onSaved() {
		return async ({
			result,
			update
		}: {
			result: { type: string };
			update: (opts?: { reset?: boolean }) => Promise<void>;
		}) => {
			if (result.type === 'success') await onChange?.();
			await update({ reset: false });
		};
	}
</script>

<form method="POST" action="?/setStatus" class="row-form" use:enhance={onSaved}>
	<input type="hidden" name="job_id" value={jobId} />
	<select class="input" name="status" bind:value={pendingStatus} style="flex:1;min-width:9rem">
		{#each APPLICATION_STATUSES as s (s)}<option value={s}>{s}</option>{/each}
	</select>
	{#if pendingStatus === 'applied'}
		<input class="input" type="date" name="next_followup_at" bind:value={followupInput} style="width:auto" />
	{/if}
	<button type="submit" class="btn">Update</button>
</form>
{#if application?.next_followup_at}
	<p class="muted small" style="margin-top:8px">
		Next follow-up: {fmtDate(application.next_followup_at)}
		{#if application.last_contact_at}· last contact {fmtDate(application.last_contact_at)}{/if}
		{#if application.outcome}· outcome: <strong>{application.outcome}</strong>{/if}
	</p>
{/if}

<div class="field-label">Unemployment claim</div>
<form method="POST" action="?/setUnemployment" class="row-form" use:enhance={onSaved}>
	<input type="hidden" name="job_id" value={jobId} />
	<input type="hidden" name="used" value={usedUnemp ? 'false' : 'true'} />
	<button type="submit" class="btn" class:primary={usedUnemp}>
		{usedUnemp ? '✓ Used for unemployment' : 'Mark used for unemployment'}
	</button>
	{#if usedUnemp && application?.used_for_unemployment_at}
		<span class="muted small">marked {fmtDate(application.used_for_unemployment_at)}</span>
	{/if}
</form>

<div class="field-label">Notes</div>
{#key jobId}
	<form method="POST" action="?/setNotes" use:enhance={onSaved}>
		<input type="hidden" name="job_id" value={jobId} />
		<textarea class="input" name="notes" rows="3" placeholder="Personal notes about this role…"
			>{application?.notes ?? ''}</textarea
		>
		<button type="submit" class="btn sm" style="margin-top:8px">Save notes</button>
	</form>
{/key}
