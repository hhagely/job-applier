import { describe, expect, it } from 'vitest';
import type { SearchProfile } from '$lib/api';
import { activeProfile, profileLabel } from './profiles';

const prof = (id: number, name: string, is_active = false) =>
	({ id, name, is_active }) as unknown as SearchProfile;

describe('profileLabel', () => {
	it('names the active profile only when there is someone to tell it apart from', () => {
		expect(profileLabel(undefined)).toBeNull();
		expect(profileLabel([prof(1, 'Default', true)])).toBeNull();
		expect(profileLabel([prof(1, 'Herb'), prof(2, 'Sam', true)])).toBe('Sam');
	});

	it('finds the active profile', () => {
		expect(activeProfile([prof(1, 'Herb'), prof(2, 'Sam', true)])?.id).toBe(2);
		expect(activeProfile([])).toBeNull();
	});
});
