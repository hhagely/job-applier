import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/stores', () => ({
	page: {
		subscribe: (fn: (v: { url: URL }) => void) => {
			fn({ url: new URL('http://localhost/') });
			return () => {};
		}
	}
}));

// The palette's posting search hits the API on every keystroke; stub it so the
// tests never reach the network (api.ts retries failed GETs, which would outlive
// the test) and so a search result can be asserted deterministically.
const { searchJobs } = vi.hoisted(() => ({ searchJobs: vi.fn() }));
vi.mock('$lib/api', () => ({
	api: { searchJobs },
	getApiBase: () => ''
}));

import CommandPalette from './CommandPalette.svelte';

describe('CommandPalette', () => {
	beforeEach(() => {
		searchJobs.mockReset();
		searchJobs.mockResolvedValue([]);
	});

	it('renders navigation + action commands when open', () => {
		render(CommandPalette, { props: { open: true, onClose: vi.fn(), onShowHelp: vi.fn() } });
		expect(screen.getByText('Go to Dashboard')).toBeInTheDocument();
		expect(screen.getByText('Go to Queue')).toBeInTheDocument();
		expect(screen.getByText('Toggle light / dark theme')).toBeInTheDocument();
	});

	it('filters as you type', async () => {
		render(CommandPalette, { props: { open: true, onClose: vi.fn(), onShowHelp: vi.fn() } });
		const input = screen.getByLabelText('Command palette input');
		await fireEvent.input(input, { target: { value: 'theme' } });
		expect(screen.getByText('Toggle light / dark theme')).toBeInTheDocument();
		expect(screen.queryByText('Go to Dashboard')).not.toBeInTheDocument();
	});

	it('renders nothing when closed', () => {
		render(CommandPalette, { props: { open: false, onClose: vi.fn(), onShowHelp: vi.fn() } });
		expect(screen.queryByText('Go to Dashboard')).not.toBeInTheDocument();
	});

	it('lists matching ingested postings above the commands', async () => {
		searchJobs.mockResolvedValue([
			{ id: 7, title: 'Staff Platform Engineer', company: { name: 'Acme' }, score: { score: 82 } }
		]);
		render(CommandPalette, { props: { open: true, onClose: vi.fn(), onShowHelp: vi.fn() } });
		const input = screen.getByLabelText('Command palette input');
		await fireEvent.input(input, { target: { value: 'acme' } });

		await waitFor(() => expect(screen.getByText('Staff Platform Engineer')).toBeInTheDocument());
		expect(screen.getByText('Acme')).toBeInTheDocument();
		expect(screen.getByText('82')).toBeInTheDocument();
		expect(searchJobs).toHaveBeenCalledWith(expect.anything(), '', 'acme');
	});

	it('does not search on a one-character query', async () => {
		render(CommandPalette, { props: { open: true, onClose: vi.fn(), onShowHelp: vi.fn() } });
		const input = screen.getByLabelText('Command palette input');
		await fireEvent.input(input, { target: { value: 'a' } });
		await new Promise((r) => setTimeout(r, 250));
		expect(searchJobs).not.toHaveBeenCalled();
	});
});
