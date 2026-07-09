<script lang="ts">
	// One row in the Queue master list. Presentational: the parent owns selection
	// and the selected/checked state and passes callbacks; this renders the row and
	// its status/dup/follow-up/unemployment/draft-list tags.
	import type { Job } from '$lib/api';
	import { draftCart } from '$lib/draftCart.svelte';
	import { isFollowupDue, isUsedForUnemployment } from '$lib/jobFilters';
	import { sourceInfo } from '$lib/sources';
	import ScoreBadge from '$lib/ScoreBadge.svelte';

	let {
		job,
		active,
		selected,
		onSelect,
		onToggleSelect
	}: {
		job: Job;
		active: boolean;
		selected: boolean;
		onSelect: () => void;
		onToggleSelect: () => void;
	} = $props();

	const si = $derived(sourceInfo(job.source));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div
	id="jrow-{job.id}"
	class="jrow"
	class:sel={active}
	class:dup={job.duplicate_of != null}
	onclick={onSelect}
>
	<input
		type="checkbox"
		checked={selected}
		onclick={(e) => e.stopPropagation()}
		onchange={onToggleSelect}
		aria-label="select job"
	/>
	<ScoreBadge score={job.score?.score ?? null} size="sm" stale={job.score?.is_stale ?? false} />
	<div class="jr-main">
		<div class="jr-title">{job.title}</div>
		<div class="jr-meta">
			<span class="pill src-{job.source}"><span class="dot-badge"></span>{si.label}</span>
			<span class="co">{job.company?.name ?? 'Unknown'}</span>
			{#if job.location}· <span>{job.location}</span>{/if}
			{#if job.application}<span class="tag status-{job.application.status}">{job.application.status}</span>{/if}
			{#if isFollowupDue(job)}<span class="tag" style="color:var(--good)">follow-up due</span>{/if}
			{#if isUsedForUnemployment(job)}<span class="tag status-applied">✓ unemployment</span>{/if}
			{#if job.duplicate_of != null}<span class="tag" style="color:var(--good)">dup #{job.duplicate_of}</span>{/if}
			{#if draftCart.has(job.id)}<span class="tag status-draft">in draft</span>{/if}
		</div>
	</div>
</div>

<style>
	.jrow {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 14px;
		border-bottom: 1px solid var(--border);
		cursor: pointer;
		position: relative;
	}
	.jrow:hover {
		background: var(--surface-2);
	}
	.jrow.sel {
		background: var(--accent-soft);
	}
	.jrow.sel::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0;
		bottom: 0;
		width: 3px;
		background: var(--accent);
	}
	.jrow.dup {
		opacity: 0.6;
	}
	.jrow.dup:hover {
		opacity: 1;
	}
	.jrow input[type='checkbox'] {
		width: 15px;
		height: 15px;
		accent-color: var(--accent);
		flex: none;
	}
	.jr-main {
		min-width: 0;
		flex: 1;
	}
	.jr-title {
		font-weight: 600;
		font-size: 13px;
		line-height: 1.35;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.jr-meta {
		display: flex;
		align-items: center;
		gap: 7px;
		margin-top: 5px;
		color: var(--faint);
		font-size: 11.5px;
		flex-wrap: wrap;
	}
	.jr-meta .co {
		color: var(--muted);
		font-weight: 560;
	}
</style>
