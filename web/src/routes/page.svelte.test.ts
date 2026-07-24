import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Application, FilterStatus, Job } from '$lib/api';
import { FILTERS_STORAGE_KEY, type PersistedFilters } from '$lib/queueFilters';

// SvelteKit ambient modules the board imports.
vi.mock('$app/environment', () => ({ browser: false }));
vi.mock('$app/forms', () => ({ enhance: () => ({}) }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/stores', () => ({
	page: {
		subscribe: (fn: (v: { url: URL }) => void) => {
			fn({ url: new URL('http://localhost/') });
			return () => {};
		}
	}
}));

import Board from './+page.svelte';

function job(overrides: Partial<Job> = {}): Job {
	return {
		id: 1,
		source: 'greenhouse',
		url: 'https://e.com/1',
		title: 'Senior Engineer',
		remote: true,
		ingested_at: new Date().toISOString(),
		filter_status: 'passed',
		score: null,
		application: null,
		duplicate_of: null,
		...overrides
	};
}

function data(overrides = {}) {
	return {
		jobs: [job()],
		filter_status: 'passed' as FilterStatus,
		include_duplicates: false,
		include_archived: false,
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 1, queue: 1, followups: 0, strong: 0 },
		update: null,
		profile: null,
		...overrides
	};
}

function application(overrides: Partial<Application> = {}): Application {
	return {
		status: 'applied',
		notes: null,
		applied_at: '2026-07-01T00:00:00Z',
		updated_at: '2026-07-01T00:00:00Z',
		next_followup_at: null,
		last_contact_at: null,
		outcome: null,
		used_for_unemployment: false,
		used_for_unemployment_at: null,
		...overrides
	} as Application;
}

/** Seed the persisted filter state the Queue restores on mount. */
function persist(filters: Partial<PersistedFilters>) {
	localStorage.setItem(
		FILTERS_STORAGE_KEY,
		JSON.stringify({
			sortBy: 'score-desc',
			statuses: [],
			eases: [],
			sources: [],
			unscoredOnly: false,
			unemployment: [],
			minScoreInput: '',
			...filters
		})
	);
}

/** A filter chip, matched on the label that precedes its count span. */
function chip(label: string): HTMLElement | undefined {
	return screen
		.queryAllByRole('button')
		.find((b) => b.className.includes('chip') && b.textContent?.trim().startsWith(label));
}

/** Job titles currently rendered in the master list. */
function listedTitles(): string[] {
	const scroll = document.querySelector('.list-scroll') as HTMLElement;
	return within(scroll)
		.queryAllByText(/./, { selector: '.jr-title' })
		.map((el) => el.textContent ?? '');
}

// Both the persisted filters and the draft cart live in localStorage; clear it so
// a seeded filter in one test cannot leak into the next.
beforeEach(() => localStorage.clear());

describe('queue header actions', () => {
	// Scrape + score moved to the Dashboard; the queue header now carries
	// filter/draft-list controls only.
	it('has no Run scrape or Score pending controls', () => {
		render(Board, { props: { data: data() } });
		expect(screen.queryByRole('button', { name: /Run scrape/ })).toBeNull();
		expect(screen.queryByRole('button', { name: /Score pending/ })).toBeNull();
	});

	it('shows a Clear filters button, disabled when no filters are active', () => {
		render(Board, { props: { data: data() } });
		expect(screen.getByRole('button', { name: /Clear filters/ })).toBeDisabled();
	});

	it('shows a disabled "Draft list empty" button when the draft list is empty', () => {
		render(Board, { props: { data: data() } });
		expect(screen.getByRole('button', { name: /Draft list empty/ })).toBeDisabled();
	});

	it('prompts to set up AI for drafting when no provider is selected', () => {
		render(Board, { props: { data: data({ aiProvider: null }) } });
		const link = screen.getByRole('link', { name: /Draft list — set up AI/ });
		expect(link).toHaveAttribute('href', '/settings');
	});
});

// Regression: filter selections persist across sessions, so a facet can empty out
// between visits (every `new` job becomes `applied`, a source stops returning
// results). Hiding a chip that is still selected turned it into a ghost filter —
// it kept emptying the queue with nothing on screen to explain why, and no way to
// switch it off.
describe('queue filter chips', () => {
	const applied = job({ id: 1, application: application({ status: 'applied' }) });

	it('keeps a selected status chip visible after its facet empties out', () => {
		persist({ statuses: ['new'] });
		render(Board, { props: { data: data({ jobs: [applied] }) } });

		const ghost = chip('new');
		expect(ghost).toBeDefined();
		expect(ghost).toHaveAttribute('aria-pressed', 'true');
		expect(ghost?.textContent).toContain('0');
	});

	it('still hides a zero-count status chip that is not selected', () => {
		render(Board, { props: { data: data({ jobs: [applied] }) } });

		expect(chip('screening')).toBeUndefined();
		expect(chip('applied')).toBeDefined();
	});

	it('keeps a selected source chip visible after that source leaves the queue', () => {
		persist({ sources: ['lever'] });
		render(Board, { props: { data: data({ jobs: [job({ id: 1, source: 'greenhouse' })] }) } });

		expect(chip('Lever')).toHaveAttribute('aria-pressed', 'true');
	});

	it('keeps a selected ease chip visible after its facet empties out', () => {
		// greenhouse is `easy`, so the `hard` facet has no jobs behind it.
		persist({ eases: ['hard'] });
		render(Board, { props: { data: data({ jobs: [job({ id: 1, source: 'greenhouse' })] }) } });

		expect(chip('hard')).toHaveAttribute('aria-pressed', 'true');
	});

	it('enables Clear filters so a restored ghost filter can be dropped', () => {
		persist({ statuses: ['new'] });
		render(Board, { props: { data: data({ jobs: [applied] }) } });

		expect(screen.getByRole('button', { name: /Clear filters/ })).toBeEnabled();
	});
});

describe('queue unemployment filter', () => {
	const jobs = [
		job({ id: 1, title: 'Unused A', application: application({ used_for_unemployment: false }) }),
		job({ id: 2, title: 'Unused B', application: application({ used_for_unemployment: false }) }),
		job({ id: 3, title: 'Reported', application: application({ used_for_unemployment: true }) })
	];

	it('counts used / not-used jobs on the chips', () => {
		render(Board, { props: { data: data({ jobs }) } });

		expect(chip('not used')?.textContent).toContain('2');
		expect(chip('used')?.textContent).toContain('1');
	});

	it('narrows the list to jobs not yet reported', async () => {
		render(Board, { props: { data: data({ jobs }) } });
		expect(listedTitles()).toHaveLength(3);

		await fireEvent.click(chip('not used')!);

		expect(listedTitles()).toEqual(['Unused A', 'Unused B']);
	});

	it('narrows the list to jobs already reported', async () => {
		render(Board, { props: { data: data({ jobs }) } });

		await fireEvent.click(chip('used')!);

		expect(listedTitles()).toEqual(['Reported']);
	});
});

describe('show-archived toggle', () => {
	it('reflects include_archived in the chip pressed state', () => {
		render(Board, { props: { data: data({ include_archived: false }) } });
		expect(chip('Show archived')).toHaveAttribute('aria-pressed', 'false');
	});

	it('is pressed when archived jobs are being shown', () => {
		render(Board, { props: { data: data({ include_archived: true }) } });
		expect(chip('Show archived')).toHaveAttribute('aria-pressed', 'true');
	});
});
