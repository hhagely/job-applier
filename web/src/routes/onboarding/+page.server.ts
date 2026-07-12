import { redirect } from '@sveltejs/kit';
import { api } from '$lib/api';
import { serverApiBase } from '$lib/apiBase.server';
import type { Actions, PageServerLoad } from './$types';

// Kept in lockstep with +layout.server.ts, which reads the same cookie to decide
// whether to redirect a resume-less user into this wizard.
const ONBOARDING_DISMISSED_COOKIE = 'ja_onboarding_dismissed';

// Seed the wizard with whatever already exists (detected providers, an active
// resume) so a partially-configured user lands on the right step. Every piece is
// defensive: a failure just yields nulls and the client falls back gracefully.
export const load: PageServerLoad = async ({ fetch }) => {
	const base = serverApiBase();

	let providers = null;
	try {
		providers = await api.getProviders(fetch, base);
	} catch {
		providers = null;
	}

	let resume = null;
	try {
		resume = await api.getCurrentResume(fetch, base);
	} catch {
		resume = null;
	}

	return { apiBase: base, providers, resume };
};

export const actions: Actions = {
	// "Skip for now" and "Finish" both land here: remember the dismissal so the
	// layout redirect doesn't pull the user back in, then go to the dashboard.
	dismiss: async ({ cookies }) => {
		cookies.set(ONBOARDING_DISMISSED_COOKIE, '1', {
			path: '/',
			maxAge: 60 * 60 * 24 * 365,
			httpOnly: false,
			sameSite: 'lax'
		});
		redirect(303, '/dashboard');
	}
};
