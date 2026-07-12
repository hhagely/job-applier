import { render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import type { UpdateInfo } from '$lib/api';

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
