import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { Job } from '$lib/api';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

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

function data() {
	return {
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 12, queue: 5, followups: 2, strong: 3 },
		kpis: { jobs: 12, scored: 8, unreviewed: 5, strong: 3, applied: 4, rejected: 1, followupsDue: 2, avg: 77 },
		dist: [
			{ label: '80–100', band: 'strong', n: 3 },
			{ label: '65–79', band: 'good', n: 4 },
			{ label: '< 65', band: 'weak', n: 1 }
		],
		bySource: [{ source: 'greenhouse', n: 7 }],
		topMatches: [job()],
		followups: []
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
