import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import type { SearchProfile } from '$lib/api';

vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

import Page from './+page.svelte';

function profile(overrides: Partial<SearchProfile> = {}): SearchProfile {
	return {
		id: 1,
		name: 'Default',
		is_active: true,
		resume_id: 1,
		role_titles: [],
		title_terms: [],
		seniority_terms: [],
		required_tech: [],
		excluded_tech: [],
		extracted_skills: [],
		home_state: null,
		recommendations_draft: null,
		updated_at: null,
		using_defaults: true,
		...overrides
	};
}

function data(overrides = {}) {
	return {
		profile: profile(),
		profiles: [profile()],
		resumes: [
			{ id: 1, original_filename: 'me.pdf', is_active: true, uploaded_at: '2026-09-01T00:00:00Z' }
		],
		hasResume: true,
		apiBase: '',
		aiProvider: 'claude' as string | null,
		counts: { jobs: 1, queue: 1, followups: 0, strong: 0 },
		blacklist: [],
		watched: [],
		countries: [{ code: 'GB', name: 'United Kingdom', aliases: ['GBR', 'UK', 'Britain'] }],
		coverage: {
			total: 1432,
			enabled: 1430,
			by_source: { greenhouse: 1003, lever: 375 },
			last_checked_at: new Date().toISOString()
		},
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

describe('search page state-of-residence field', () => {
	it('renders the state dropdown with the privacy disclaimer', () => {
		render(Page, { props: { data: data(), form: null } });

		const select = screen.getByRole('combobox', { name: /State of residence/i });
		expect(select).toBeInTheDocument();
		// Must submit under name="home_state" so the +page.server.ts save action's
		// readProfile() picks it up; a rename here would silently break persistence.
		expect(select).toHaveAttribute('name', 'home_state');
		expect(screen.getByRole('option', { name: 'Missouri' })).toBeInTheDocument();
		expect(screen.getByText(/only.*to filter jobs during ingest/i)).toBeInTheDocument();
	});

	it('preselects the saved home state', () => {
		render(Page, { props: { data: data({ profile: profile({ home_state: 'California' }) }), form: null } });

		const select = screen.getByRole('combobox', { name: /State of residence/i }) as HTMLSelectElement;
		expect(select.value).toBe('California');
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

describe('search page company check', () => {
	it('shows the check-a-company card with no list until something is added', () => {
		render(Page, { props: { data: data(), form: null } });

		expect(screen.getByText('Check a company')).toBeInTheDocument();
		expect(screen.getByPlaceholderText('Company name or job-board URL')).toBeInTheDocument();
		expect(screen.queryByText(/Added this way/i)).not.toBeInTheDocument();
	});

	it('lists added companies with their board and a remove control', () => {
		const props = data({
			watched: [
				{
					id: 3,
					source: 'greenhouse',
					slug: 'acme',
					label: 'Acme',
					enabled: true,
					last_job_count: 12,
					last_error: null,
					added_at: ''
				}
			]
		});
		render(Page, { props: { data: props, form: null } });

		expect(screen.getByText('Acme')).toBeInTheDocument();
		expect(screen.getByText(/greenhouse \/ acme/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Remove Acme/ })).toBeInTheDocument();
		expect(screen.getByText(/Added this way/i)).toBeInTheDocument();
	});

	it('shows the already-searched notice as a warning, not an error', () => {
		render(Page, {
			props: {
				data: data(),
				form: {
					companyOk: true,
					companyAlready: true,
					companyMessage: 'Acme is already in your search list (greenhouse / acme) — not added again.'
				}
			}
		});

		expect(screen.getByText(/Acme is already in your search list/)).toBeInTheDocument();
	});
});

describe('search page Where you can work', () => {
	const marketsField = (container: HTMLElement) =>
		JSON.parse((container.querySelector('input[name="markets"]') as HTMLInputElement).value);
	const toggle = () => screen.getByRole('switch', { name: /Search outside the United States/ });

	async function addCountry(text: string) {
		await fireEvent.input(screen.getByLabelText('Add a country'), { target: { value: text } });
		await fireEvent.click(screen.getByRole('button', { name: 'Add country' }));
	}

	it('posts no other countries until the toggle is on', () => {
		const { container } = render(Page, { props: { data: data(), form: null } });
		expect(screen.queryByLabelText('Add a country')).not.toBeInTheDocument();
		expect(marketsField(container)).toEqual([]);
	});

	it('adds a country by alias and posts its settings', async () => {
		const { container } = render(Page, { props: { data: data(), form: null } });
		await fireEvent.click(toggle());
		await addCountry('uk');

		expect(screen.getByText('United Kingdom')).toBeInTheDocument();
		await fireEvent.click(screen.getByLabelText(/visa sponsorship/));
		await fireEvent.input(screen.getByPlaceholderText(/Edinburgh, Glasgow/), {
			target: { value: 'Edinburgh, Glasgow' }
		});
		expect(marketsField(container)).toEqual([
			{ country: 'GB', needs_sponsorship: true, hybrid_cities: ['Edinburgh', 'Glasgow'] }
		]);
	});

	it('rejects a country outside the list', async () => {
		render(Page, { props: { data: data(), form: null } });
		await fireEvent.click(toggle());
		await addCountry('Narnia');
		expect(screen.getByText(/isn't a country we can search/)).toBeInTheDocument();
	});

	it('seeds the editor from the saved profile', () => {
		const saved = profile({
			markets: [{ country: 'GB', needs_sponsorship: false, hybrid_cities: ['Glasgow'] }]
		});
		const { container } = render(Page, { props: { data: data({ profile: saved }), form: null } });
		expect(toggle()).toHaveAttribute('aria-checked', 'true');
		expect(marketsField(container)).toEqual([
			{ country: 'GB', needs_sponsorship: false, hybrid_cities: ['Glasgow'] }
		]);
	});
});
