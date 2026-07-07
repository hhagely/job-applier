<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { Job } from '$lib/api';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function daysOverdue(iso: string | null | undefined): number {
		if (!iso) return 0;
		return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000));
	}

	function fmtDate(iso: string | null | undefined): string {
		return iso ? new Date(iso).toLocaleDateString() : '—';
	}

	function appliedAt(job: Job): string | null | undefined {
		return job.application?.applied_at;
	}
	function followupDate(job: Job): string | null | undefined {
		return job.application?.next_followup_at;
	}

	const overdueCount = $derived(data.jobs.filter((j) => daysOverdue(followupDate(j)) >= 14).length);

	let submitting = $state<number | null>(null);
	function onSubmit(id: number) {
		submitting = id;
		return async ({ update }: { update: (opts?: { reset?: boolean }) => Promise<void> }) => {
			submitting = null;
			await invalidateAll();
			await update({ reset: false });
		};
	}
</script>

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
		<div class="fu-list">
			{#each data.jobs as job (job.id)}
				{@const overdue = daysOverdue(followupDate(job))}
				<div class="fu-card">
					<div class="fu-main">
						<a href={`/jobs/${job.id}`} class="fu-title">{job.title}</a>
						<div class="fu-sub">
							<span>{job.company?.name ?? 'Unknown'}</span>
							· <span>applied {fmtDate(appliedAt(job))}</span>
							· <span class="fu-over" class:soon={overdue < 14}>
								{overdue === 0 ? 'due today' : `${overdue}d overdue`}
							</span>
						</div>
					</div>
					<div class="fu-actions">
						<form method="POST" action="?/contacted" use:enhance={() => onSubmit(job.id)}>
							<input type="hidden" name="id" value={job.id} />
							<button type="submit" class="btn sm primary" disabled={submitting === job.id}>Mark contacted</button>
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
			{/each}
		</div>
	{/if}
</div>

<style>
	.fu-list {
		display: flex;
		flex-direction: column;
		gap: 11px;
		max-width: 920px;
	}
	.fu-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
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
