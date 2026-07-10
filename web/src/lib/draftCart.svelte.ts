import { browser } from '$app/environment';

const STORAGE_KEY = 'job-applier:draft-cart';

function load(): number[] {
	if (!browser) return [];
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		return parsed.filter((v): v is number => Number.isInteger(v));
	} catch {
		return [];
	}
}

function persist(ids: number[]): void {
	if (!browser) return;
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
	} catch {
		// quota / privacy mode — ignore
	}
}

class DraftCart {
	ids = $state<number[]>(load());

	has(id: number): boolean {
		return this.ids.includes(id);
	}

	add(id: number): void {
		if (this.has(id)) return;
		this.ids = [...this.ids, id];
		persist(this.ids);
	}

	remove(id: number): void {
		const next = this.ids.filter((v) => v !== id);
		if (next.length === this.ids.length) return;
		this.ids = next;
		persist(this.ids);
	}

	toggle(id: number): void {
		if (this.has(id)) this.remove(id);
		else this.add(id);
	}

	clear(): void {
		this.ids = [];
		persist(this.ids);
	}
}

export const draftCart = new DraftCart();
