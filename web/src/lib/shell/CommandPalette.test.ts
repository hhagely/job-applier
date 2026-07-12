import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/stores', () => ({
	page: {
		subscribe: (fn: (v: { url: URL }) => void) => {
			fn({ url: new URL('http://localhost/') });
			return () => {};
		}
	}
}));

import CommandPalette from './CommandPalette.svelte';

describe('CommandPalette', () => {
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
});
