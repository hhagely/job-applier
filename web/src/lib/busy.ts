import type { SubmitFunction } from '@sveltejs/kit';

/** A `use:enhance` callback that flags a form busy for the length of its
 * submit: `set(true)` as it starts, `set(false)` once SvelteKit has applied the
 * result. */
export const whileBusy =
	(set: (busy: boolean) => void): SubmitFunction =>
	() => {
		set(true);
		return async ({ update }) => {
			await update();
			set(false);
		};
	};
