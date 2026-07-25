// Client-side mirror of the Electron auto-update state (electron-updater runs in
// the main process; see desktop/main.js + preload.js). Single source of truth for
// the titlebar pill, the update popover, and the Settings "About & updates" card —
// mirrors the design prototype's paintUpdate()/installUpdate()/checkForUpdates().
//
// Two-phase, user-driven: a launch check surfaces availability (pill appears), the
// user taps Download (progress), then Restart (quitAndInstall). In a plain browser
// (no `window.desktop.updater`) it stays inert and nothing renders.

import { browser } from '$app/environment';
import { desktop, type UpdaterEvent, type UpdateDetails } from './desktop';
import { toast } from './toast.svelte';

const fmtMB = (b?: number): string => (b ? (b / 1048576).toFixed(1) + ' MB' : '');

// Dev-only preview (see initFake): `?fakeUpdate=1` seeds this sample so the pill +
// popover + Settings card can be seen without a packaged build / real release.
const FAKE_KEY = 'ja-fake-update';
const FAKE_INFO = {
	version: '2.1.0',
	releaseDate: '2026-07-22',
	sizeBytes: Math.round(48.2 * 1024 * 1024),
	notes: [
		'Rapid-triage keys in the queue — A apply · R reject · D draft, inbox-style.',
		'Match rationale now renders as matched / missing skill chips.',
		'Fixes light-theme contrast on progress tracks and score badges.'
	]
};

export class Updater {
	available = $state(false);
	downloaded = $state(false);
	downloading = $state(false);
	version = $state(''); // raw, e.g. "2.1.0"; UI prepends "v"
	size = $state('');
	date = $state('');
	notes = $state<string[]>([]);
	percent = $state(0);
	checkedLabel = $state(''); // #updChecked text
	popoverOpen = $state(false);
	fake = $state(false); // dev preview mode (see initFake); always false in prod

	// Guards backfill: don't let a stale cached event clobber a live one that
	// already arrived (the launch check fires around the same time as mount).
	private applied = false;

	constructor() {
		if (!browser) return;
		const bridge = desktop()?.updater;
		if (bridge) {
			bridge.onEvent((e) => this.apply(e));
			bridge
				.getState()
				.then((e) => {
					if (!this.applied && e) this.apply(e);
				})
				.catch(() => {});
		}
		this.initFake();
	}

	/** Whether the Electron auto-update bridge is present (vs. a plain browser). */
	get present(): boolean {
		return browser && !!desktop()?.updater;
	}

	/** The running app version (raw, no "v"), from the preload bridge. */
	get currentVersion(): string {
		return desktop()?.version ?? '';
	}

	private apply(e: UpdaterEvent): void {
		if (!e || !e.type) return;
		this.applied = true;
		switch (e.type) {
			case 'checking':
				this.checkedLabel = 'checking…';
				break;
			case 'available':
				this.setInfo(e.info);
				this.available = true;
				this.downloaded = false;
				this.downloading = false;
				this.percent = 0;
				this.checkedLabel = 'just now';
				break;
			case 'not-available':
				this.available = false;
				this.downloaded = false;
				this.downloading = false;
				this.checkedLabel = 'just now';
				toast('You’re on the latest version');
				break;
			case 'progress':
				this.downloading = true;
				this.percent = Math.round(e.percent ?? 0);
				break;
			case 'downloaded':
				this.setInfo(e.info);
				this.downloaded = true;
				this.downloading = false;
				this.percent = 100;
				toast('v' + this.version + ' downloaded — restart to apply');
				break;
			case 'error':
				this.downloading = false;
				toast('Update error: ' + (e.message ?? 'unknown'));
				break;
		}
	}

	private setInfo(info?: UpdateDetails): void {
		if (!info) return;
		if (info.version) this.version = info.version;
		this.size = fmtMB(info.sizeBytes);
		this.date = info.releaseDate ? new Date(info.releaseDate).toLocaleDateString() : '';
		this.notes = info.notes ?? [];
	}

	// --- popover UI ---------------------------------------------------------
	openPopover(): void {
		this.popoverOpen = true;
	}
	closePopover(): void {
		this.popoverOpen = false;
	}
	togglePopover(): void {
		this.popoverOpen = !this.popoverOpen;
	}

	// --- actions ------------------------------------------------------------
	// The design's installUpdate(): first tap downloads, second tap restarts.
	install(): void {
		if (this.downloading) return;
		if (this.downloaded) {
			toast('Restarting to install v' + this.version + '…');
			if (!this.fake) desktop()?.updater?.install();
			return;
		}
		if (this.fake) {
			this.simulateDownload();
			return;
		}
		this.downloading = true;
		this.percent = 0;
		desktop()
			?.updater?.download()
			.catch(() => {
				this.downloading = false;
				toast('Download failed');
			});
	}

	check(): void {
		if (this.fake) {
			this.checkedLabel = 'checking…';
			toast('Checking for updates…');
			setTimeout(() => this.apply({ type: 'available', info: FAKE_INFO }), 600);
			return;
		}
		if (!this.present) return; // desktop-only; inert in a plain browser
		this.checkedLabel = 'checking…';
		toast('Checking for updates…');
		desktop()
			?.updater?.check()
			.catch(() => toast('Update check failed'));
	}

	// --- dev preview --------------------------------------------------------
	// `?fakeUpdate=1` (persisted in localStorage) seeds a sample update so the whole
	// UI — pill, popover, Settings card — is visible without a packaged build or a
	// real GitHub release; `?fakeUpdate=0` clears it. Gated on import.meta.env.DEV,
	// so this branch is never compiled into a packaged (production) bundle.
	private initFake(): void {
		if (!import.meta.env.DEV) return;
		try {
			const param = new URL(window.location.href).searchParams.get('fakeUpdate');
			if (param === '1') localStorage.setItem(FAKE_KEY, '1');
			else if (param === '0') localStorage.removeItem(FAKE_KEY);
			if (localStorage.getItem(FAKE_KEY) !== '1') return;
		} catch {
			return;
		}
		this.fake = true;
		this.apply({ type: 'available', info: FAKE_INFO });
	}

	// Mimics the design prototype's fake download so the two-phase flow is clickable.
	private simulateDownload(): void {
		this.downloading = true;
		this.percent = 0;
		const iv = setInterval(() => {
			const next = Math.min(100, this.percent + 11);
			this.apply({ type: 'progress', percent: next });
			if (next >= 100) {
				clearInterval(iv);
				this.apply({ type: 'downloaded', info: FAKE_INFO });
			}
		}, 180);
	}
}

export const updater = new Updater();
