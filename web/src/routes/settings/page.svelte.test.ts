import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { Provider } from '$lib/api';

// use:enhance needs a runtime action; a no-op is enough for render tests.
vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

import Page from './+page.svelte';

function makeData(overrides = {}) {
	return {
		apiBase: '',
		aiProvider: 'claude',
		counts: { jobs: 1, queue: 1, followups: 0, strong: 0 },
		update: null,
		profile: null,
		ai: {
			providers: [
				{
					name: 'claude',
					display_name: 'Claude Code',
					tier: 'recommended',
					available: true,
					version: '2.1',
					scoring_models: [
						{ value: 'haiku', label: 'Haiku - fastest' },
						{ value: 'sonnet', label: 'Sonnet - balanced (default)' }
					],
					scoring_model_default: 'sonnet'
				},
				{
					name: 'gemini',
					display_name: 'Gemini CLI',
					tier: 'recommended',
					available: true,
					version: '1.0',
					scoring_models: [{ value: 'gemini-2.5-flash', label: '2.5 Flash - balanced' }],
					scoring_model_default: 'gemini-2.5-flash'
				},
				{
					name: 'ollama',
					display_name: 'Ollama (local)',
					tier: 'best-effort',
					available: false,
					version: null,
					scoring_models: []
				}
			] as Provider[],
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
		expect(screen.getAllByText('recommended')).toHaveLength(2);

		// Available provider selectable; unavailable one disabled.
		const claudeRadio = screen.getByRole('radio', { name: /Claude Code/ });
		const ollamaRadio = screen.getByRole('radio', { name: /Ollama/ });
		expect(claudeRadio).toBeEnabled();
		expect(ollamaRadio).toBeDisabled();

		// Test round-trip section shows when a provider is selected.
		expect(screen.getByText(/Test round-trip/i)).toBeInTheDocument();
	});

	it('offers the selected provider’s scoring models, defaulting to its own default', () => {
		render(Page, { props: { data: makeData(), form: null } });

		const select = screen.getByRole('combobox', { name: /Scoring model/i });
		expect([...select.querySelectorAll('option')].map((o) => o.textContent)).toEqual([
			'Default (sonnet)',
			'Haiku - fastest',
			'Sonnet - balanced (default)',
			'Custom…'
		]);
		// No persisted override -> "Default", and the submitted value stays blank so
		// the backend keeps resolving the provider default.
		expect((select as HTMLSelectElement).value).toBe('');
		expect(document.querySelector('input[name="scoring_model"]')).toHaveValue('');
	});

	it('repopulates the model list when a different provider is picked', async () => {
		render(Page, { props: { data: makeData(), form: null } });

		await fireEvent.click(screen.getByRole('radio', { name: /Gemini/ }));

		const select = screen.getByRole('combobox', { name: /Scoring model/i });
		expect([...select.querySelectorAll('option')].map((o) => o.textContent)).toEqual([
			'Default (gemini-2.5-flash)',
			'2.5 Flash - balanced',
			'Custom…'
		]);
	});

	it('preselects Custom for a saved override the provider does not list', () => {
		const base = makeData();
		const data = { ...base, ai: { ...base.ai, scoring_model: 'claude-haiku-4-5' } };
		render(Page, { props: { data, form: null } });

		const select = screen.getByRole('combobox', { name: /Scoring model/i });
		expect((select as HTMLSelectElement).selectedOptions[0].textContent).toBe('Custom…');
		// The unlisted value survives round-tripping rather than being dropped.
		expect(screen.getByRole('textbox', { name: /Custom scoring model/i })).toHaveValue(
			'claude-haiku-4-5'
		);
		expect(document.querySelector('input[name="scoring_model"]')).toHaveValue('claude-haiku-4-5');
	});

	it('offers a one-click reset only while an override is set', () => {
		const base = makeData();
		// No override -> nothing to reset; "Default" in the dropdown already covers it.
		render(Page, { props: { data: base, form: null } });
		expect(screen.queryByRole('button', { name: /Reset to default/i })).toBeNull();
		cleanup();

		// With an override, the always-safe recovery is one labelled click, and it
		// posts to its own action rather than relying on the user re-picking Default.
		const data = { ...base, ai: { ...base.ai, scoring_model: 'gpt-9000' } };
		render(Page, { props: { data, form: null } });
		const reset = screen.getByRole('button', { name: /Reset to default \(sonnet\)/i });
		expect(reset).toHaveAttribute('formaction', '?/resetScoringModel');
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
						version: null,
						scoring_models: []
					}
				] as Provider[],
				selected: null,
				model: null
			}
		});
		render(Page, { props: { data, form: null } });

		expect(screen.getByText(/No AI CLI detected/i)).toBeInTheDocument();
		expect(screen.queryByText(/Test round-trip/i)).not.toBeInTheDocument();
	});
});
