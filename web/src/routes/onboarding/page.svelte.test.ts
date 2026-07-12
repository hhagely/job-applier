import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ProvidersResponse } from '$lib/api';

// use:enhance / form submits need a runtime action; a no-op is enough for render.
vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

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
});
