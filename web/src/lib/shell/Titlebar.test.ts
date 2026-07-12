import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Titlebar from './Titlebar.svelte';

describe('Titlebar', () => {
	it('exposes theme toggle + palette + help, and hides window controls in a browser', () => {
		render(Titlebar, { props: { onOpenPalette: vi.fn(), onOpenHelp: vi.fn() } });
		expect(screen.getByLabelText('Toggle light/dark theme')).toBeInTheDocument();
		expect(screen.getByLabelText('Open command palette')).toBeInTheDocument();
		expect(screen.getByLabelText('Keyboard shortcuts')).toBeInTheDocument();
		// No Electron bridge in jsdom -> no min/max/close chrome.
		expect(screen.queryByLabelText('Close')).not.toBeInTheDocument();
	});

	it('opens the palette when the search box is clicked', async () => {
		const onOpenPalette = vi.fn();
		render(Titlebar, { props: { onOpenPalette, onOpenHelp: vi.fn() } });
		await fireEvent.click(screen.getByLabelText('Open command palette'));
		expect(onOpenPalette).toHaveBeenCalledOnce();
	});
});
