// Form-field parsing and failure mapping shared by the routes' form actions.
import { ApiError } from '$lib/api';

/** A row id from a form field: a positive integer, or null for anything else
 * (missing, blank, non-numeric, fractional, zero or negative). */
export function formId(form: FormData, name = 'id'): number | null {
	const id = Number(form.get(name));
	return Number.isInteger(id) && id > 0 ? id : null;
}

/** The HTTP status a form action's `fail()` should carry for a failed API call:
 * the API's own status (404 missing, 409 refused, 422 invalid, 503 busy), or 502
 * when the call never got an answer (network error, unexpected throw). */
export function failStatus(e: unknown): number {
	return e instanceof ApiError && e.status >= 400 && e.status <= 599 ? e.status : 502;
}
