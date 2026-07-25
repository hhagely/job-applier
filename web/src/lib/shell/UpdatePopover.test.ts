import { render, screen, fireEvent } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import { updater } from '$lib/updater.svelte';
import UpdatePopover from './UpdatePopover.svelte';

function reset() {
	updater.available = false;
	updater.downloaded = false;
	updater.downloading = false;
	updater.version = '';
	updater.size = '';
	updater.date = '';
	updater.notes = [];
	updater.percent = 0;
	updater.popoverOpen = false;
}
afterEach(reset);

describe('UpdatePopover', () => {
	it('renders version, size and notes when open', () => {
		updater.available = true;
		updater.version = '2.1.0';
		updater.size = '48.2 MB';
		updater.notes = ['Rapid-triage keys in the queue'];
		updater.popoverOpen = true;
		render(UpdatePopover);

		expect(document.querySelector('#upVer')?.textContent).toBe('v2.1.0');
		expect(document.querySelector('#upPop')?.classList.contains('show')).toBe(true);
		expect(screen.getByText('Rapid-triage keys in the queue')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /Download & install/i })).toBeInTheDocument();
	});

	it('falls back to a generic note when the release carries none', () => {
		updater.available = true;
		updater.version = '2.1.0';
		updater.popoverOpen = true;
		render(UpdatePopover);
		expect(screen.getByText(/Bug fixes and improvements/i)).toBeInTheDocument();
	});

	it('flips the primary action to Restart to install once downloaded', () => {
		updater.available = true;
		updater.version = '2.1.0';
		updater.downloaded = true;
		updater.popoverOpen = true;
		render(UpdatePopover);
		expect(screen.getByRole('button', { name: /Restart to install/i })).toBeInTheDocument();
	});

	it('Later dismisses the popover', async () => {
		updater.available = true;
		updater.version = '2.1.0';
		updater.popoverOpen = true;
		render(UpdatePopover);
		await fireEvent.click(screen.getByRole('button', { name: /Later/i }));
		expect(updater.popoverOpen).toBe(false);
	});
});
