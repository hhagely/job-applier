// Client-side mirror of the Electron auto-update state (electron-updater runs in
// the main process; see desktop/main.js + preload.js). Single source of truth for
// the titlebar pill, the update popover, and the Settings "About & updates" card —
// mirrors the design prototype's paintUpdate()/installUpdate()/checkForUpdates().
//
// Two-phase, user-driven: a launch check surfaces availability (pill appears), the
// user taps Download (progress), then Restart (quitAndInstall). In a plain browser
// (no `window.desktop.updater`) it stays inert: the pill and popover never render,
// and the Settings "About & updates" card hides its update rows behind `present`
// (the version line still renders — it falls back to /api/version via the loader).

import { browser } from '$app/environment';
import { desktop, type UpdaterEvent, type UpdateDetails } from './desktop';
import { toast } from './toast.svelte';

const fmtMB = (b?: number): string => (b ? (b / 1048576).toFixed(1) + ' MB' : '');

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

	// Guards backfill: don't let a stale cached event clobber a live one that
	// already arrived (the launch check fires around the same time as mount).
	private applied = false;

	// Set by check(); cleared by the next terminal event. The launch check must stay
	// quiet when already up to date (it fires on every start), but a check the user
	// explicitly asked for needs an answer either way.
	private userCheck = false;

	constructor() {
		if (!browser) return;
		const bridge = desktop()?.updater;
		if (!bridge) return;
		bridge.onEvent((e) => this.apply(e));
		bridge
			.getState()
			.then((e) => {
				if (!this.applied && e) this.apply(e);
			})
			.catch(() => {});
	}

	/** Whether the Electron auto-update bridge is present (vs. a plain browser). */
	get present(): boolean {
		return browser && !!desktop()?.updater;
	}

	/** The running app version (raw, no "v"), from the preload bridge. */
	get currentVersion(): string {
		return desktop()?.version ?? '';
	}

	/**
	 * GitHub release page for the pending update — what "release notes" links to.
	 * Falls back to /latest before a version is known. The Electron shell opens
	 * non-loopback URLs in the OS browser (see registerExternalLinks in main.js).
	 */
	get releaseUrl(): string {
		const releases = 'https://github.com/hhagely/job-applier/releases';
		return this.version ? `${releases}/tag/v${this.version}` : `${releases}/latest`;
	}

	private apply(e: UpdaterEvent): void {
		if (!e || !e.type) return;
		this.applied = true;
		switch (e.type) {
			case 'checking':
				this.checkedLabel = 'checking…';
				break;
			case 'available': {
				// electron-updater re-emits update-available on EVERY check, including
				// for a version that is already downloaded. Only clear the download
				// state when the pending version actually changed, or a "Check now"
				// after downloading would revert the button to "Download & install"
				// while autoInstallOnAppQuit is still armed to install on quit.
				const next = e.info?.version;
				if (next && next !== this.version) {
					this.downloaded = false;
					this.downloading = false;
					this.percent = 0;
				}
				this.setInfo(e.info);
				this.available = true;
				this.checkedLabel = 'just now';
				this.userCheck = false;
				break;
			}
			case 'not-available':
				this.available = false;
				this.downloaded = false;
				this.downloading = false;
				this.percent = 0;
				this.checkedLabel = 'just now';
				if (this.userCheck) toast('You’re on the latest version');
				this.userCheck = false;
				break;
			case 'progress':
				// `available` too: the main process caches only the LAST event, so a
				// window reload replays this one on its own. Without it the pill and
				// the Settings install row (both gated on `available`) stay hidden.
				this.available = true;
				this.downloading = true;
				this.percent = Math.round(e.percent ?? 0);
				break;
			case 'downloaded':
				this.setInfo(e.info);
				this.available = true; // see 'progress' — a reload replays this alone
				this.downloaded = true;
				this.downloading = false;
				this.percent = 100;
				toast('v' + this.version + ' downloaded — restart to apply');
				break;
			case 'error':
				this.downloading = false;
				this.percent = 0;
				// Clear 'checking…' or the Settings card reads "Last checked checking…."
				this.checkedLabel = '';
				this.userCheck = false;
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
			desktop()?.updater?.install();
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
		if (!this.present) return; // desktop-only; inert in a plain browser
		this.userCheck = true;
		this.checkedLabel = 'checking…';
		toast('Checking for updates…');
		desktop()
			?.updater?.check()
			.catch(() => {
				this.checkedLabel = '';
				this.userCheck = false;
				toast('Update check failed');
			});
	}
}

export const updater = new Updater();
