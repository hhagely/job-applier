import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { Job } from '$lib/api';

// SvelteKit ambient modules the dashboard imports (scrape/score forms + command bus).
vi.mock('$app/environment', () => ({ browser: false }));
vi.mock('$app/forms', () => ({ enhance: () => ({}) }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));

import Page from './+page.svelte';

function job(overrides: Partial<Job> = {}): Job {
	return {
		id: 1,
		source: 'greenhouse',
		url: 'https://e.com/1',
		title: 'Senior Engineer',
		remote: true,
		ingested_at: new Date().toISOString(),
		filter_status: 'passed',
		company: { id: 1, name: 'Acme', is_blocked: false },
		score: {
			score: 88,
			rubric: {},
			scored_by: 'claude-cli',
			scored_at: new Date().toISOString(),
			score_kind: 'baseline',
			is_stale: false
		},
		application: null,
		duplicate_of: null,
		...overrides
	};
}

function data(overrides = {}) {
	return {
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 12, queue: 5, followups: 2, strong: 3 },
		update: null,
		kpis: { jobs: 12, scored: 8, unreviewed: 5, strong: 3, applied: 4, rejected: 1, followupsDue: 2, avg: 77 },
		pending: 1,
		dist: [
			{ label: '80–100', band: 'strong', n: 3 },
			{ label: '65–79', band: 'good', n: 4 },
			{ label: '< 65', band: 'weak', n: 1 }
		],
		bySource: [{ source: 'greenhouse', n: 7 }],
		topMatches: [job()],
		followups: [],
		...overrides
	};
}

describe('dashboard', () => {
	it('renders KPI values and the top-matches list', () => {
		render(Page, { props: { data: data() } });
		expect(screen.getByText('Strong matches')).toBeInTheDocument();
		expect(screen.getByText('Top unreviewed matches')).toBeInTheDocument();
		expect(screen.getByText('Senior Engineer')).toBeInTheDocument();
		// score badge from the shared component
		expect(screen.getByText('88')).toBeInTheDocument();
	});
});

describe('dashboard Score-pending button', () => {
	it('prompts to set up AI when no provider is selected', () => {
		render(Page, { props: { data: data({ aiProvider: null }) } });
		const link = screen.getByRole('link', { name: /Score pending — set up AI/ });
		expect(link).toHaveAttribute('href', '/settings');
	});

	it('shows an enabled "Score pending (N)" button when a provider is set', () => {
		render(Page, { props: { data: data({ pending: 1 }) } });
		expect(screen.getByRole('button', { name: /Score pending \(1\)/ })).toBeEnabled();
	});

	it('disables the button when nothing is pending', () => {
		render(Page, { props: { data: data({ pending: 0 }) } });
		expect(screen.getByRole('button', { name: /Score pending \(0\)/ })).toBeDisabled();
	});
});

describe('dashboard Run-scrape button', () => {
	it('always shows an enabled Run scrape button (no provider needed)', () => {
		render(Page, { props: { data: data({ aiProvider: null }) } });
		expect(screen.getByRole('button', { name: /Run scrape/ })).toBeEnabled();
	});
});
