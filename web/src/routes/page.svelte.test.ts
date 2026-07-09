import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { FilterStatus, Job } from '$lib/api';

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
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 1, queue: 1, followups: 0, strong: 0 },
		update: null,
		...overrides
	};
}

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
