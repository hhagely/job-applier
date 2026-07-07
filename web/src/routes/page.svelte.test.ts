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
		...overrides
	};
}

describe('board Score-pending button', () => {
	it('prompts to set up AI when no provider is selected', () => {
		render(Board, { props: { data: data({ aiProvider: null }) } });
		const link = screen.getByRole('link', { name: /Score pending — set up AI/ });
		expect(link).toHaveAttribute('href', '/settings');
	});

	it('shows an enabled "Score pending (N)" button when a provider is set', () => {
		render(Board, { props: { data: data() } });
		const btn = screen.getByRole('button', { name: /Score pending \(1\)/ });
		expect(btn).toBeEnabled();
	});

	it('disables the button when nothing is pending', () => {
		const scored: Partial<Job> = {
			score: {
				score: 80,
				rubric: {},
				scored_by: 'claude-cli',
				scored_at: new Date().toISOString(),
				score_kind: 'baseline',
				is_stale: false
			}
		};
		render(Board, { props: { data: data({ jobs: [job(scored)] }) } });
		expect(screen.getByRole('button', { name: /Score pending \(0\)/ })).toBeDisabled();
	});
});

describe('board Run-scrape button', () => {
	it('always shows an enabled Run scrape button (no provider needed)', () => {
		render(Board, { props: { data: data({ aiProvider: null }) } });
		expect(screen.getByRole('button', { name: /Run scrape/ })).toBeEnabled();
	});
});
