import { render, screen, fireEvent } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateInfo } from '$lib/api';
import { updater } from '$lib/updater.svelte';

import UpdateBanner from './UpdateBanner.svelte';

const AVAILABLE: UpdateInfo = {
	current: '0.1.0',
	latest: 'v0.2.0',
	update_available: true,
	url: 'https://github.com/hhagely/job-applier/releases/latest'
};

afterEach(() => localStorage.clear());

describe('UpdateBanner', () => {
	it('renders when an update is available', async () => {
		render(UpdateBanner, { props: { update: AVAILABLE } });
		expect(await screen.findByText(/Update available/i)).toBeInTheDocument();
		expect(screen.getByText('v0.2.0')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /Open Releases/i })).toHaveAttribute(
			'href',
			AVAILABLE.url
		);
	});

	it('renders nothing when no update is available', () => {
		render(UpdateBanner, {
			props: { update: { ...AVAILABLE, update_available: false } }
		});
		expect(screen.queryByText(/Update available/i)).not.toBeInTheDocument();
	});

	it('renders nothing when update info is null (offline / fail-soft)', () => {
		render(UpdateBanner, { props: { update: null } });
		expect(screen.queryByText(/Update available/i)).not.toBeInTheDocument();
	});

	it('stays hidden once the same version has been dismissed', () => {
		localStorage.setItem('ja-update-dismissed', 'v0.2.0');
		render(UpdateBanner, { props: { update: AVAILABLE } });
		// onMount reads the dismissal synchronously in jsdom; banner never shows.
		expect(screen.queryByText(/Update available/i)).not.toBeInTheDocument();
	});

	it('re-appears for a newer version even after an older one was dismissed', async () => {
		localStorage.setItem('ja-update-dismissed', 'v0.2.0');
		render(UpdateBanner, { props: { update: { ...AVAILABLE, latest: 'v0.3.0' } } });
		expect(await screen.findByText(/Update available/i)).toBeInTheDocument();
	});
});

describe('UpdateBanner (Electron auto-update)', () => {
	const install = vi.fn();

	beforeEach(() => {
		(window as unknown as { desktop?: unknown }).desktop = {
			updater: {
				install,
				onEvent: () => () => {},
				getState: async () => ({ state: 'idle' }),
				check: async () => ({ state: 'idle' })
			}
		};
	});

	afterEach(() => {
		delete (window as unknown as { desktop?: unknown }).desktop;
		updater.event = { state: 'idle' };
		install.mockClear();
	});

	it('shows a download progress bar while downloading', () => {
		updater.event = { state: 'downloading', version: 'v0.2.0', percent: 42 };
		render(UpdateBanner, { props: { update: AVAILABLE } });
		expect(screen.getByText(/Downloading update/i)).toBeInTheDocument();
		expect(screen.getByText('42%')).toBeInTheDocument();
		// The manual "Open Releases" link is suppressed while auto-update drives.
		expect(screen.queryByRole('link', { name: /Open Releases/i })).not.toBeInTheDocument();
	});

	it('offers Restart & install once downloaded and wires the action', async () => {
		updater.event = { state: 'downloaded', version: 'v0.2.0' };
		render(UpdateBanner, { props: { update: AVAILABLE } });
		expect(screen.getByText(/Update ready/i)).toBeInTheDocument();
		const btn = screen.getByRole('button', { name: /Restart & install/i });
		await fireEvent.click(btn);
		expect(install).toHaveBeenCalledTimes(1);
	});

	it('falls back to the manual link when the auto-updater errors (e.g. .deb)', async () => {
		updater.event = { state: 'error', error: 'cannot self-update' };
		render(UpdateBanner, { props: { update: AVAILABLE } });
		expect(await screen.findByText(/Update available/i)).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /Open Releases/i })).toBeInTheDocument();
	});
});
