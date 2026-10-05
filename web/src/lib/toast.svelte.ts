// Minimal ephemeral toast store. The auto-update flow uses it for check/error
// feedback ("Checking for updates…", "You're on the latest version", download
// errors); kept generic so anything can `toast(msg)`. Rendered by Toaster.svelte.

import { browser } from '$app/environment';
import { untrack } from 'svelte';

export interface ToastItem {
	id: number;
	message: string;
}

class Toaster {
	items = $state<ToastItem[]>([]);
	private seq = 0;

	push(message: string, ms = 3600): void {
		if (!browser || !message) return;
		const id = ++this.seq;
		// Untracked read: callers toast from inside $effects (the layout's
		// lost-connection notice), and a tracked read of `items` there makes the
		// effect depend on the very state it writes — an infinite update loop.
		this.items = [...untrack(() => this.items), { id, message }];
		setTimeout(() => this.dismiss(id), ms);
	}

	dismiss(id: number): void {
		this.items = this.items.filter((t) => t.id !== id);
	}
}

export const toasts = new Toaster();

/** Fire-and-forget toast. */
export function toast(message: string): void {
	toasts.push(message);
}
