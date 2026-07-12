import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ProvidersResponse } from '$lib/api';

// use:enhance / form submits need a runtime action; a no-op is enough for render.
vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

// The wizard's state autosave calls the typed client directly (client-side island);
// stub just the two methods it touches so we can assert the merged save payload.
vi.mock('$lib/api', () => ({
	getApiBase: () => '',
	api: { getSearchProfile: vi.fn(), saveSearchProfile: vi.fn() }
}));

import { api } from '$lib/api';
import Page from './+page.svelte';

function makeData(overrides = {}) {
	return {
		// Layout data is merged into page data by SvelteKit; include its fields.
		update: null,
		profile: null,
		aiProvider: null,
		counts: { jobs: null, queue: null, followups: null, strong: null },
		apiBase: '',
		providers: {
			providers: [
				{ name: 'claude', display_name: 'Claude Code', tier: 'recommended', available: true, version: '2.1' },
				{ name: 'ollama', display_name: 'Ollama (local)', tier: 'best-effort', available: false, version: null }
			],
			selected: null,
			model: null
		} as ProvidersResponse,
		resume: null,
		searchProfile: null,
		...overrides
	};
}

describe('onboarding wizard', () => {
	it('starts on the AI provider step and lists detected providers', () => {
		render(Page, { props: { data: makeData() } });
		expect(screen.getByRole('heading', { name: /Welcome to job-applier/i })).toBeInTheDocument();
		expect(screen.getByText('Claude Code')).toBeInTheDocument();
		// An available provider is selectable; an unavailable one is disabled.
		expect(screen.getByRole('radio', { name: /Claude Code/ })).toBeEnabled();
		expect(screen.getByRole('radio', { name: /Ollama/ })).toBeDisabled();
	});

	it('shows install guidance when no AI CLI is available and still lets you continue', () => {
		const data = makeData({
			providers: {
				providers: [
					{ name: 'claude', display_name: 'Claude Code', tier: 'recommended', available: false, version: null }
				],
				selected: null,
				model: null
			} as ProvidersResponse
		});
		render(Page, { props: { data } });
		expect(screen.getByText(/No AI CLI detected/i)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Continue without AI/i })).toBeInTheDocument();
	});

	it('always offers a skip-setup escape hatch (not a hard gate)', () => {
		render(Page, { props: { data: makeData() } });
		expect(screen.getByRole('button', { name: /Skip setup for now/i })).toBeInTheDocument();
	});

	it('offers the optional state-of-residence selector on the fetch-jobs step', async () => {
		render(Page, { props: { data: makeData() } });
		// Advance to step 3 (AI provider -> Resume -> Fetch jobs).
		await fireEvent.click(screen.getByRole('button', { name: /Skip this step/i }));
		await fireEvent.click(screen.getByRole('button', { name: /Skip this step/i }));

		expect(screen.getByRole('combobox', { name: /State of residence/i })).toBeInTheDocument();
		expect(screen.getByRole('option', { name: 'Missouri' })).toBeInTheDocument();
		expect(screen.getByText(/stored locally, never sent anywhere/i)).toBeInTheDocument();
	});

	it('preselects a home state already saved on the profile', async () => {
		render(Page, { props: { data: makeData({ searchProfile: { home_state: 'California' } }) } });
		await fireEvent.click(screen.getByRole('button', { name: /Skip this step/i }));
		await fireEvent.click(screen.getByRole('button', { name: /Skip this step/i }));

		const select = screen.getByRole('combobox', { name: /State of residence/i }) as HTMLSelectElement;
		expect(select.value).toBe('California');
	});

	it('autosaves the picked state, merged into the existing profile', async () => {
		const cur = {
			role_titles: ['Senior Engineer'],
			seniority_terms: ['senior'],
			required_tech: ['typescript'],
			excluded_tech: ['angular'],
			extracted_skills: ['react'],
			home_state: null
		};
		vi.mocked(api.getSearchProfile).mockResolvedValue(cur as never);
		vi.mocked(api.saveSearchProfile).mockResolvedValue({ ...cur, home_state: 'Texas' } as never);

		render(Page, { props: { data: makeData() } });
		await fireEvent.click(screen.getByRole('button', { name: /Skip this step/i }));
		await fireEvent.click(screen.getByRole('button', { name: /Skip this step/i }));

		const select = screen.getByRole('combobox', { name: /State of residence/i });
		await fireEvent.change(select, { target: { value: 'Texas' } });

		// The chosen state is persisted, and the other profile fields are carried
		// through untouched (the save is a full replace, so the merge must preserve them).
		await vi.waitFor(() =>
			expect(api.saveSearchProfile).toHaveBeenCalledWith(
				expect.anything(),
				expect.anything(),
				expect.objectContaining({
					role_titles: ['Senior Engineer'],
					seniority_terms: ['senior'],
					required_tech: ['typescript'],
					home_state: 'Texas'
				})
			)
		);
	});
});
