<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { Job } from '$lib/api';
	import { daysOverdue, fmtDate, formatOverdue } from '$lib/date';
	import { daysSinceContact } from '$lib/jobFilters';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function appliedAt(job: Job): string | null | undefined {
		return job.application?.applied_at;
	}
	function followupDate(job: Job): string | null | undefined {
		return job.application?.next_followup_at;
	}

	const overdueCount = $derived(data.due.filter((j) => daysOverdue(followupDate(j)) >= 14).length);

	let submitting = $state<number | null>(null);
	function onSubmit(id: number) {
		submitting = id;
		return async ({ update }: { update: (opts?: { reset?: boolean }) => Promise<void> }) => {
			submitting = null;
			await invalidateAll();
			await update({ reset: false });
		};
	}

	// Bulk close-out. Two steps rather than one: this flips every row in the group
	// at once and there is no undo, so an accidental click on a 31-row backlog
	// would be a bad afternoon. The count is in the confirm label so what is about
	// to happen is unambiguous.
	let confirmingAll = $state(false);
	let submittingAll = $state(false);
	function onSubmitAll() {
		submittingAll = true;
		return async ({ update }: { update: (opts?: { reset?: boolean }) => Promise<void> }) => {
			submittingAll = false;
			confirmingAll = false;
			await invalidateAll();
			await update({ reset: false });
		};
	}
</script>

{#snippet card(job: Job, ghosted: boolean)}
	{@const overdue = daysOverdue(followupDate(job))}
	<div class="card fu-card">
		<div class="fu-main">
			<a href={`/jobs/${job.id}`} class="fu-title">{job.title}</a>
			<div class="fu-sub">
				<span>{job.company?.name ?? 'Unknown'}</span>
				· <span>applied {fmtDate(appliedAt(job))}</span>
				· <span class="fu-over" class:soon={!ghosted && overdue < 14}>
					{ghosted ? `silent ${daysSinceContact(job)}d` : formatOverdue(overdue)}
				</span>
			</div>
		</div>
		<div class="fu-actions">
			{#if ghosted}
				<form method="POST" action="?/noResponse" use:enhance={() => onSubmit(job.id)}>
					<input type="hidden" name="id" value={job.id} />
					<button type="submit" class="btn sm primary" disabled={submitting === job.id}>No response</button>
				</form>
			{/if}
			<form method="POST" action="?/contacted" use:enhance={() => onSubmit(job.id)}>
				<input type="hidden" name="id" value={job.id} />
				<button type="submit" class="btn sm" class:primary={!ghosted} disabled={submitting === job.id}>Mark contacted</button>
			</form>
			<form method="POST" action="?/snooze" use:enhance={() => onSubmit(job.id)}>
				<input type="hidden" name="id" value={job.id} />
				<input type="hidden" name="days" value="7" />
				<button type="submit" class="btn sm" disabled={submitting === job.id}>Snooze 7d</button>
			</form>
			<form method="POST" action="?/rejected" use:enhance={() => onSubmit(job.id)}>
				<input type="hidden" name="id" value={job.id} />
				<button type="submit" class="btn sm danger" disabled={submitting === job.id}>Rejected</button>
			</form>
			<form method="POST" action="?/setOutcome" class="outcome-form" use:enhance={() => onSubmit(job.id)}>
				<input type="hidden" name="id" value={job.id} />
				<input class="mini-input fu-outcome" type="text" name="outcome" placeholder="outcome (phone screen…)" required />
				<button type="submit" class="btn sm" disabled={submitting === job.id}>Set</button>
			</form>
		</div>
	</div>
{/snippet}

<div class="view-head">
	<div class="vh-titles">
		<h1>Follow-ups</h1>
		<div class="vh-sub">
			<b class="num">{data.jobs.length}</b> due
			{#if overdueCount > 0}· <span style="color:var(--weak)">{overdueCount} overdue by 14+ days</span>{/if}
		</div>
	</div>
</div>

<div class="view-body">
	{#if data.jobs.length === 0}
		<p class="banner" style="max-width:920px">Nothing overdue. Nice.</p>
	{:else}
		{#if data.ghosted.length > 0}
			<section class="fu-group">
				<div class="fu-group-head">
					<h2>Ghosted? <span class="fu-group-count">{data.ghosted.length}</span></h2>
					<form
						method="POST"
						action="?/noResponseAll"
						class="fu-group-action"
						use:enhance={onSubmitAll}
					>
						{#each data.ghosted as job (job.id)}
							<input type="hidden" name="ids" value={job.id} />
						{/each}
						{#if confirmingAll}
							<span class="fu-confirm-q">Mark all {data.ghosted.length} as no response?</span>
							<button type="submit" class="btn sm primary" disabled={submittingAll}>
								{submittingAll ? 'Marking…' : 'Yes, mark them'}
							</button>
							<button
								type="button"
								class="btn sm ghost"
								disabled={submittingAll}
								onclick={() => (confirmingAll = false)}
							>
								Cancel
							</button>
						{:else}
							<button type="button" class="btn sm" onclick={() => (confirmingAll = true)}>
								Mark all as no response
							</button>
						{/if}
					</form>
				</div>
				<p class="fu-group-note">
					Applied {data.ghostedAfterDays}+ days ago, no reply. Marking these
					<b>no response</b> closes them out without recording a rejection nobody sent, so
					your rejection count stays honest.
				</p>
				<div class="fu-list">
					{#each data.ghosted as job (job.id)}
						{@render card(job, true)}
					{/each}
				</div>
			</section>
		{/if}
		{#if data.due.length > 0}
			<div class="fu-list">
				{#each data.due as job (job.id)}
					{@render card(job, false)}
				{/each}
			</div>
		{/if}
	{/if}
</div>

<style>
	.fu-list {
		display: flex;
		flex-direction: column;
		gap: 11px;
		max-width: 920px;
	}
	.fu-group {
		max-width: 920px;
		margin-bottom: 26px;
	}
	.fu-group-head {
		display: flex;
		align-items: center;
		gap: 10px;
		margin: 0 0 5px;
		flex-wrap: wrap;
	}
	.fu-group-head h2 {
		font-size: 13.5px;
		font-weight: 640;
		margin: 0;
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.fu-group-action {
		display: flex;
		align-items: center;
		gap: 7px;
		margin: 0 0 0 auto;
	}
	.fu-confirm-q {
		font-size: 12px;
		color: var(--fg);
		font-weight: 560;
	}
	.fu-group-count {
		font-family: var(--mono);
		font-size: 11.5px;
		font-weight: 640;
		color: var(--weak);
		background: var(--weak-soft);
		border-radius: 999px;
		padding: 1px 8px;
	}
	.fu-group-note {
		font-size: 12px;
		color: var(--faint);
		margin: 0 0 11px;
		max-width: 60ch;
		line-height: 1.5;
	}
	.fu-card {
		padding: 14px 16px;
		display: flex;
		align-items: center;
		gap: 16px;
		flex-wrap: wrap;
	}
	.fu-main {
		min-width: 220px;
		flex: 1;
	}
	.fu-title {
		font-weight: 600;
		font-size: 13.5px;
		color: var(--fg);
	}
	.fu-title:hover {
		color: var(--accent);
		text-decoration: none;
	}
	.fu-sub {
		font-size: 12px;
		color: var(--faint);
		margin-top: 4px;
		display: flex;
		gap: 6px;
		align-items: center;
		flex-wrap: wrap;
	}
	.fu-over {
		color: var(--weak);
		font-weight: 640;
		font-family: var(--mono);
		font-size: 11.5px;
	}
	.fu-over.soon {
		color: var(--good);
	}
	.fu-actions {
		display: flex;
		gap: 7px;
		align-items: center;
		flex-wrap: wrap;
	}
	.fu-actions form {
		margin: 0;
	}
	.outcome-form {
		display: inline-flex;
		gap: 6px;
		align-items: center;
	}
	.fu-outcome {
		width: 160px;
		height: 27px;
	}
</style>
