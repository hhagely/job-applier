// Tiny event bus so the command palette (in the root layout) can trigger
// page-scoped actions (run scrape, score pending) that live as form actions on
// the dashboard page. The palette navigates to the page, then emits; the page
// listens and submits the matching form.

import { browser } from '$app/environment';

export type ShellCommand = 'scrape' | 'score';

const EVENT = 'ja:command';

export function emitCommand(name: ShellCommand): void {
	if (browser) window.dispatchEvent(new CustomEvent<ShellCommand>(EVENT, { detail: name }));
}

export function onCommand(handler: (name: ShellCommand) => void): () => void {
	if (!browser) return () => {};
	const listener = (e: Event) => handler((e as CustomEvent<ShellCommand>).detail);
	window.addEventListener(EVENT, listener);
	return () => window.removeEventListener(EVENT, listener);
}
