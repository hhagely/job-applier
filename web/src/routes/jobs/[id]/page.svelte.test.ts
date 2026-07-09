import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { Draft, JobDetail } from '$lib/api';

vi.mock('$app/forms', () => ({ enhance: () => ({}) }));
vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn() }));

import Page from './+page.svelte';

function job(): JobDetail {
	return {
		id: 7,
		source: 'greenhouse',
		url: 'https://e.com/7',
		title: 'Senior Engineer',
		remote: true,
		ingested_at: new Date().toISOString(),
		filter_status: 'passed',
		score: null,
		application: null,
		duplicate_of: null,
		description: 'We use TypeScript.'
	};
}

function draft(): Draft {
	return {
		job_id: 7,
		has_resume_md: false,
		has_resume_pdf: false,
		has_cover_letter_md: false,
		has_cover_letter_pdf: false,
		updated_at: null
	};
}

function data(overrides = {}) {
	return {
		job: job(),
		draft: draft(),
		scoreHistory: [],
		canonical: null,
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 1, queue: 1, followups: 0, strong: 0 },
		...overrides
	};
}

describe('job detail Generate-draft button', () => {
	it('prompts to set up AI when no provider is selected', () => {
		render(Page, { props: { data: data({ aiProvider: null }) } });
		expect(
			screen.getByRole('link', { name: /Generate tailored draft — set up AI/ })
		).toHaveAttribute('href', '/settings');
	});

	it('shows the generate button when a provider is selected', () => {
		render(Page, { props: { data: data() } });
		expect(
			screen.getByRole('button', { name: /Generate tailored draft/ })
		).toBeInTheDocument();
	});

	it('labels the button "Regenerate" once a draft exists', () => {
		const withDraft = { ...draft(), has_resume_md: true };
		render(Page, { props: { data: data({ draft: withDraft }) } });
		expect(
			screen.getByRole('button', { name: /Regenerate tailored draft/ })
		).toBeInTheDocument();
	});
});
