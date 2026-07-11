import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { SearchProfile } from '$lib/api';

vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

import Page from './+page.svelte';

function profile(overrides: Partial<SearchProfile> = {}): SearchProfile {
	return {
		id: 1,
		role_titles: [],
		seniority_terms: [],
		required_tech: [],
		excluded_tech: [],
		extracted_skills: [],
		recommendations_draft: null,
		updated_at: null,
		using_defaults: true,
		...overrides
	};
}

function data(overrides = {}) {
	return {
		profile: profile(),
		hasResume: true,
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 1, queue: 1, followups: 0, strong: 0 },
		update: null,
		blacklist: [],
		...overrides
	};
}

describe('search page Suggest-roles button', () => {
	it('prompts to set up AI when no provider is selected', () => {
		render(Page, { props: { data: data({ aiProvider: null }), form: null } });
		expect(screen.getByRole('link', { name: /Suggest roles — set up AI/ })).toHaveAttribute(
			'href',
			'/settings'
		);
	});

	it('enables the suggest button when a provider and resume exist', () => {
		render(Page, { props: { data: data(), form: null } });
		expect(screen.getByRole('button', { name: /Suggest roles from resume/ })).toBeEnabled();
	});

	it('disables the suggest button without a resume', () => {
		render(Page, { props: { data: data({ hasResume: false }), form: null } });
		expect(screen.getByRole('button', { name: /Suggest roles from resume/ })).toBeDisabled();
	});

	it('renders a returned recommendation draft for accept/reject', () => {
		const rec = {
			role_titles: ['Staff Backend Engineer'],
			seniority_terms: ['staff'],
			required_tech: ['node'],
			excluded_tech: ['angular'],
			extracted_skills: ['Node.js'],
			rationale: 'Strong backend background.'
		};
		render(Page, {
			props: { data: data({ profile: profile({ recommendations_draft: rec }) }), form: null }
		});
		expect(screen.getByText('Recommendations')).toBeInTheDocument();
		expect(screen.getByText(/Staff Backend Engineer/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Replace with these/ })).toBeInTheDocument();
	});
});

describe('search page company blacklist', () => {
	it('shows the blacklist card with an empty state', () => {
		render(Page, { props: { data: data(), form: null } });

		expect(screen.getByText('Company blacklist')).toBeInTheDocument();
		expect(screen.getByPlaceholderText('Company name')).toBeInTheDocument();
		expect(screen.getByText(/No companies blacklisted yet/i)).toBeInTheDocument();
	});

	it('lists blacklisted companies with their reason and a remove control', () => {
		const props = data({
			blacklist: [
				{ id: 1, name: 'Evil Corp', normalized_name: 'evil', reason: 'no remote', created_at: '' },
				{ id: 2, name: 'Globex', normalized_name: 'globex', reason: null, created_at: '' }
			]
		});
		render(Page, { props: { data: props, form: null } });

		expect(screen.getByText('Evil Corp')).toBeInTheDocument();
		expect(screen.getByText('no remote')).toBeInTheDocument();
		expect(screen.getByText('Globex')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Remove Evil Corp/ })).toBeInTheDocument();
		expect(screen.queryByText(/No companies blacklisted yet/i)).not.toBeInTheDocument();
	});
});
