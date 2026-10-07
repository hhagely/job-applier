import type { SearchProfile } from '$lib/api';

/** The active profile, or null when the list is empty (e.g. the API was down). */
export function activeProfile(profiles: readonly SearchProfile[] | undefined): SearchProfile | null {
	return profiles?.find((p) => p.is_active) ?? null;
}

/**
 * The active profile's name, but only when there's more than one profile — a
 * single-person install has no one to tell apart, so pages stay uncluttered.
 */
export function profileLabel(profiles: readonly SearchProfile[] | undefined): string | null {
	return profiles && profiles.length > 1 ? (activeProfile(profiles)?.name ?? null) : null;
}
