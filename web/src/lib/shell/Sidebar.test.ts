import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/forms', () => ({ enhance: () => ({}) }));
vi.mock('$app/state', () => ({ page: { url: new URL('http://localhost/jobs/3?tab=x') } }));

import type { SearchProfile } from '$lib/api';
import Sidebar from './Sidebar.svelte';

const prof = (id: number, name: string, is_active = false) =>
	({ id, name, is_active, resume_id: null }) as unknown as SearchProfile;

describe('Sidebar profile menu', () => {
	it('shows the active profile even when it is the only one', () => {
		render(Sidebar, { props: { profiles: [prof(1, 'Default', true)], profile: null } });
		const trigger = screen.getByRole('button', { name: /Default/ });
		expect(trigger).toHaveAttribute('aria-expanded', 'false');
		expect(screen.getByText('No resume yet')).toBeInTheDocument();
	});

	it('lists every profile as a switch back to this page, with the active one disabled', async () => {
		render(Sidebar, {
			props: {
				profiles: [prof(1, 'Herb', true), prof(2, 'Sam')],
				profile: { name: 'Herb Hagely', subtitle: 'Engineer', initials: 'HH' }
			}
		});
		await fireEvent.click(screen.getByRole('button', { name: /Herb/ }));

		const herb = screen.getByRole('menuitemradio', { name: /Herb/ });
		const sam = screen.getByRole('menuitemradio', { name: /Sam/ });
		expect(herb).toBeDisabled();
		expect(herb).toHaveAttribute('aria-checked', 'true');
		expect(sam).not.toBeDisabled();
		expect(sam).toHaveAttribute('name', 'id');
		expect(sam).toHaveAttribute('value', '2');

		const form = sam.closest('form')!;
		expect(form.getAttribute('action')).toBe('/search?/activateProfile');
		expect(form.querySelector<HTMLInputElement>('input[name="redirect_to"]')!.value).toBe(
			'/jobs/3?tab=x'
		);
		expect(screen.getByRole('menuitem', { name: /Herb's resumes/ })).toHaveAttribute('href', '/resume');
		expect(screen.getByRole('menuitem', { name: /manage profiles/i })).toHaveAttribute(
			'href',
			'/search#profiles'
		);
	});

	it('closes on Escape', async () => {
		render(Sidebar, { props: { profiles: [prof(1, 'Herb', true)], profile: null } });
		await fireEvent.click(screen.getByRole('button', { name: /Herb/ }));
		expect(screen.getByRole('menu')).toBeInTheDocument();
		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(screen.queryByRole('menu')).not.toBeInTheDocument();
	});
});
