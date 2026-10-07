import { beforeEach, describe, expect, it, vi } from 'vitest';
import { isRedirect, type RequestEvent } from '@sveltejs/kit';

vi.mock('$lib/apiBase.server', () => ({ serverApiBase: () => 'http://test' }));

const { api } = vi.hoisted(() => ({
	api: {
		createSearchProfile: vi.fn(),
		activateSearchProfile: vi.fn(),
		updateSearchProfileMeta: vi.fn(),
		deleteSearchProfile: vi.fn(),
		saveSearchProfile: vi.fn(),
		getSearchProfile: vi.fn(),
		clearRecommendations: vi.fn()
	}
}));
vi.mock('$lib/api', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api')>()),
	api
}));

import { ApiError } from '$lib/api';
import { actions } from './+page.server';

type Failure = { status: number; data: { profileError: string } };

function event(form: Record<string, string> = {}): RequestEvent {
	const fd = new FormData();
	for (const [k, v] of Object.entries(form)) fd.set(k, v);
	return { request: { formData: async () => fd }, fetch: vi.fn() } as unknown as RequestEvent;
}

// The actions are typed for SvelteKit's generated route types; tests call them
// with a hand-built event.
const run = (name: keyof typeof actions, form: Record<string, string>) =>
	(actions[name] as (e: RequestEvent) => Promise<unknown>)(event(form));

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
});

describe('profile actions reject a bad id before calling the API', () => {
	it.each([
		['activateProfile', {}],
		['activateProfile', { id: 'abc' }],
		['updateProfileMeta', { id: '0' }],
		['deleteProfile', { id: '-3' }],
		['deleteProfile', { id: '1.5' }]
	] as const)('%s %j -> 400', async (name, form) => {
		const r = (await run(name, form)) as Failure;
		expect(r.status).toBe(400);
		expect(r.data.profileError).toBe('Bad profile id.');
		expect(Object.values(api).every((fn) => fn.mock.calls.length === 0)).toBe(true);
	});
});

describe('createProfile', () => {
	it('fails 400 on a blank name', async () => {
		const r = (await run('createProfile', { name: '   ' })) as Failure;
		expect(r.status).toBe(400);
		expect(api.createSearchProfile).not.toHaveBeenCalled();
	});

	it('drops an invalid clone_from instead of sending it', async () => {
		api.createSearchProfile.mockResolvedValue({ name: 'Partner' });
		await run('createProfile', { name: ' Partner ', clone_from: 'abc' });
		expect(api.createSearchProfile).toHaveBeenCalledWith(
			expect.anything(),
			'http://test',
			'Partner',
			undefined
		);
	});

	it('passes a valid clone_from through', async () => {
		api.createSearchProfile.mockResolvedValue({ name: 'Copy' });
		await run('createProfile', { name: 'Copy', clone_from: '2' });
		expect(api.createSearchProfile).toHaveBeenCalledWith(expect.anything(), 'http://test', 'Copy', 2);
	});

	it("passes the API's status and reason through", async () => {
		api.createSearchProfile.mockRejectedValue(new ApiError('nope', 404, 'search profile 9 not found'));
		const r = (await run('createProfile', { name: 'X', clone_from: '9' })) as Failure;
		expect(r.status).toBe(404);
		expect(r.data.profileError).toContain('search profile 9 not found');
	});

	it('maps a call that never got an answer to 502', async () => {
		api.createSearchProfile.mockRejectedValue(new TypeError('fetch failed'));
		const r = (await run('createProfile', { name: 'X' })) as Failure;
		expect(r.status).toBe(502);
		expect(r.data.profileError).toContain('fetch failed');
	});
});

describe('activateProfile redirect_to', () => {
	beforeEach(() => {
		api.activateSearchProfile.mockResolvedValue({ name: 'B' });
	});

	it('redirects 303 to a same-origin path', async () => {
		const thrown = await run('activateProfile', { id: '2', redirect_to: '/jobs/3' }).catch((e) => e);
		expect(isRedirect(thrown)).toBe(true);
		expect(thrown).toMatchObject({ status: 303, location: '/jobs/3' });
	});

	it.each(['//evil.com', 'https://evil.com', 'evil.com', ''])(
		'does not redirect to %j',
		async (target) => {
			const r = await run('activateProfile', { id: '2', redirect_to: target });
			expect(r).toEqual({ profileOk: true, profileMessage: 'Switched to "B".' });
		}
	);

	it('does not redirect when activation fails', async () => {
		api.activateSearchProfile.mockRejectedValue(new ApiError('gone', 404, 'search profile 2 not found'));
		const r = (await run('activateProfile', { id: '2', redirect_to: '/' })) as Failure;
		expect(r.status).toBe(404);
	});
});

describe('updateProfileMeta', () => {
	it('renames, refusing a blank name', async () => {
		const r = (await run('updateProfileMeta', { id: '2', name: '  ' })) as Failure;
		expect(r.status).toBe(400);
		expect(api.updateSearchProfileMeta).not.toHaveBeenCalled();

		api.updateSearchProfileMeta.mockResolvedValue({});
		await run('updateProfileMeta', { id: '2', name: ' Sam ' });
		expect(api.updateSearchProfileMeta).toHaveBeenCalledWith(expect.anything(), 'http://test', 2, {
			name: 'Sam'
		});
	});
});

describe('deleteProfile', () => {
	it('passes the API refusal (deleting the active profile) through as 409', async () => {
		api.deleteSearchProfile.mockRejectedValue(
			new ApiError('conflict', 409, "can't delete the active profile")
		);
		const r = (await run('deleteProfile', { id: '1' })) as Failure;
		expect(r.status).toBe(409);
		expect(r.data.profileError).toContain("can't delete the active profile");
	});
});

describe('title keywords', () => {
	const saved = () => api.saveSearchProfile.mock.calls[0][2];

	it('save splits the textarea into title_terms', async () => {
		await run('save', { title_terms: 'project manager\nprogram manager, scrum master' });
		expect(saved().title_terms).toEqual(['project manager', 'program manager', 'scrum master']);
	});

	describe('acceptDraft', () => {
		beforeEach(() => {
			api.getSearchProfile.mockResolvedValue({
				role_titles: [],
				title_terms: ['project manager'],
				seniority_terms: [],
				required_tech: [],
				excluded_tech: [],
				extracted_skills: [],
				home_state: 'Missouri',
				recommendations_draft: { title_terms: ['Project Manager', 'program manager'] }
			});
		});

		it('append merges title_terms case-insensitively', async () => {
			await run('acceptDraft', { mode: 'append' });
			expect(saved().title_terms).toEqual(['project manager', 'program manager']);
			expect(saved().home_state).toBe('Missouri');
		});

		it('replace takes the draft title_terms', async () => {
			await run('acceptDraft', { mode: 'replace' });
			expect(saved().title_terms).toEqual(['Project Manager', 'program manager']);
		});
	});
});
