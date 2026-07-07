import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

// use:enhance needs a runtime action; a no-op is enough for render tests.
vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

import Page from './+page.svelte';

function makeData(overrides = {}) {
	return {
		apiBase: '',
		aiProvider: 'claude',
		ai: {
			providers: [
				{
					name: 'claude',
					display_name: 'Claude Code',
					tier: 'recommended',
					available: true,
					version: '2.1'
				},
				{
					name: 'ollama',
					display_name: 'Ollama (local)',
					tier: 'best-effort',
					available: false,
					version: null
				}
			],
			selected: 'claude',
			model: 'llama3.1'
		},
		...overrides
	};
}

describe('settings page', () => {
	it('renders detected providers with tier badges and a test section', () => {
		render(Page, { props: { data: makeData(), form: null } });

		expect(screen.getByText('Claude Code')).toBeInTheDocument();
		expect(screen.getByText('Ollama (local)')).toBeInTheDocument();
		expect(screen.getByText('recommended')).toBeInTheDocument();

		// Available provider selectable; unavailable one disabled.
		const claudeRadio = screen.getByRole('radio', { name: /Claude Code/ });
		const ollamaRadio = screen.getByRole('radio', { name: /Ollama/ });
		expect(claudeRadio).toBeEnabled();
		expect(ollamaRadio).toBeDisabled();

		// Test round-trip section shows when a provider is selected.
		expect(screen.getByText(/Test round-trip/i)).toBeInTheDocument();
	});

	it('shows the empty state and hides the test section when no CLI is available', () => {
		const data = makeData({
			aiProvider: null,
			ai: {
				providers: [
					{
						name: 'claude',
						display_name: 'Claude Code',
						tier: 'recommended',
						available: false,
						version: null
					}
				],
				selected: null,
				model: null
			}
		});
		render(Page, { props: { data, form: null } });

		expect(screen.getByText(/No AI CLI detected/i)).toBeInTheDocument();
		expect(screen.queryByText(/Test round-trip/i)).not.toBeInTheDocument();
	});
});
