import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchProfile } from '$lib/api';

vi.mock('$app/forms', () => ({ enhance: () => ({}) }));

import ProfilesCard from './ProfilesCard.svelte';

const prof = (id: number, name: string, is_active = false) =>
	({ id, name, is_active, resume_filename: null }) as unknown as SearchProfile;

describe('ProfilesCard delete', () => {
	afterEach(() => vi.restoreAllMocks());

	function openDialog() {
		const submit = vi.spyOn(HTMLFormElement.prototype, 'requestSubmit').mockImplementation(() => {});
		render(ProfilesCard, { props: { profiles: [prof(1, 'Herb', true), prof(2, 'Sam')] } });
		return { submit, click: fireEvent.click(screen.getByRole('button', { name: 'Delete Sam' })) };
	}

	it('names the profile and says what goes and what stays', async () => {
		const { submit, click } = openDialog();
		await click;
		const dialog = screen.getByRole('dialog', { name: 'Delete "Sam"?' });
		expect(dialog).toHaveTextContent(/resumes, statuses, notes, scores, blacklist, and tailored drafts/);
		expect(dialog).toHaveTextContent('Jobs are kept.');
		expect(submit).not.toHaveBeenCalled();
	});

	it('submits that row\'s ?/deleteProfile form when confirmed', async () => {
		const { submit, click } = openDialog();
		await click;
		const dialog = screen.getByRole('dialog');
		await fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

		await waitFor(() => expect(submit).toHaveBeenCalledOnce());
		const form = submit.mock.contexts[0] as HTMLFormElement;
		expect(form.getAttribute('action')).toBe('?/deleteProfile');
		expect(new FormData(form).get('id')).toBe('2');
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('submits nothing when cancelled', async () => {
		const { submit, click } = openDialog();
		await click;
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(screen.queryByRole('dialog')).toBeNull();
		expect(submit).not.toHaveBeenCalled();
	});
});
