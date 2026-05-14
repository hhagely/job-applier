<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { Job } from '$lib/api';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function daysOverdue(iso: string | null | undefined): number {
		if (!iso) return 0;
		const diff = Date.now() - new Date(iso).getTime();
		return Math.max(0, Math.floor(diff / 86_400_000));
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleDateString();
	}

	function appliedAt(job: Job): string | null | undefined {
		return job.application?.applied_at;
	}

	function followupDate(job: Job): string | null | undefined {
		return job.application?.next_followup_at;
	}

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

<section class="header-row">
	<h1>Follow-ups</h1>
	<span class="count">{data.jobs.length} due</span>
</section>

{#if data.jobs.length === 0}
	<p class="empty">Nothing overdue. Nice.</p>
{:else}
	<ul class="jobs">
		{#each data.jobs as job (job.id)}
			{@const overdue = daysOverdue(followupDate(job))}
			<li class="row">
				<div class="main">
					<a href={`/jobs/${job.id}`} class="title">{job.title}</a>
					<div class="meta">
						<span class="company">{job.company?.name ?? 'Unknown'}</span>
						<span class="dot">·</span>
						<span>applied {fmtDate(appliedAt(job))}</span>
						<span class="dot">·</span>
						<span class="overdue" data-strong={overdue >= 14}>
							{overdue === 0 ? 'due today' : `${overdue}d overdue`}
						</span>
					</div>
				</div>
				<div class="actions">
					<form
						method="POST"
						action="?/contacted"
						use:enhance={() => onSubmit(job.id)}
					>
						<input type="hidden" name="id" value={job.id} />
						<button type="submit" class="btn primary" disabled={submitting === job.id}>
							Mark contacted today
						</button>
					</form>
					<form
						method="POST"
						action="?/snooze"
						use:enhance={() => onSubmit(job.id)}
					>
						<input type="hidden" name="id" value={job.id} />
						<input type="hidden" name="days" value="7" />
						<button type="submit" class="btn" disabled={submitting === job.id}>
							Snooze 7d
						</button>
					</form>
					<form
						method="POST"
						action="?/rejected"
						use:enhance={() => onSubmit(job.id)}
					>
						<input type="hidden" name="id" value={job.id} />
						<button type="submit" class="btn danger" disabled={submitting === job.id}>
							Mark rejected
						</button>
					</form>
					<form
						method="POST"
						action="?/setOutcome"
						class="outcome-form"
						use:enhance={() => onSubmit(job.id)}
					>
						<input type="hidden" name="id" value={job.id} />
						<input
							type="text"
							name="outcome"
							placeholder="outcome (phone screen, offer…)"
							required
						/>
						<button type="submit" class="btn" disabled={submitting === job.id}>
							Set
						</button>
					</form>
				</div>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.header-row {
		display: flex;
		align-items: baseline;
		gap: 1rem;
		margin-bottom: 1rem;
	}
	h1 {
		font-size: 1.4rem;
		margin: 0;
	}
	.count {
		color: var(--muted);
		font-size: 0.9rem;
	}
	.empty {
		color: var(--muted);
		padding: 2rem;
		text-align: center;
		border: 1px dashed var(--panel-border);
		border-radius: 8px;
	}
	.jobs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.row {
		background: var(--panel);
		border: 1px solid var(--panel-border);
		border-radius: 8px;
		padding: 0.85rem 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}
	.main {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.title {
		font-weight: 600;
		font-size: 1rem;
		color: var(--fg);
	}
	.meta {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
		color: var(--muted);
		font-size: 0.85rem;
	}
	.dot {
		color: var(--panel-border);
	}
	.overdue {
		color: var(--warn);
	}
	.overdue[data-strong='true'] {
		color: var(--bad);
		font-weight: 600;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-items: center;
	}
	.outcome-form {
		display: inline-flex;
		gap: 0.3rem;
		align-items: center;
	}
	.outcome-form input {
		background: #20262d;
		color: var(--fg);
		border: 1px solid var(--panel-border);
		border-radius: 4px;
		padding: 0.3rem 0.5rem;
		font-size: 0.85rem;
		min-width: 14rem;
	}
	.btn {
		background: var(--panel-border);
		color: var(--fg);
		border: 1px solid var(--panel-border);
		border-radius: 6px;
		padding: 0.35rem 0.75rem;
		font-size: 0.85rem;
		cursor: pointer;
	}
	.btn:hover {
		border-color: var(--accent);
	}
	.btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.btn.primary {
		background: rgba(88, 166, 255, 0.18);
		color: var(--accent);
		border-color: var(--accent);
	}
	.btn.danger {
		background: rgba(248, 81, 73, 0.16);
		color: var(--bad);
		border-color: rgba(248, 81, 73, 0.4);
	}
</style>
