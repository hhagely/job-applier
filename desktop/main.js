// Electron main process for the job-applier desktop shell (Phase 6).
//
// Replaces the Phase 1 `app-dev` Python launcher: pick free ports, spawn the
// backend (from source in dev, the PyInstaller sidecar when packaged), run the
// SvelteKit adapter-node handler in-process, health-check the backend, then show
// the window. PDFs render via Electron's printToPDF (an offscreen window), so the
// packaged app ships no Playwright.

const { app, BrowserWindow, dialog, ipcMain, session, shell } = require('electron');
const { autoUpdater } = require('electron-updater');
const { spawn, execSync } = require('node:child_process');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');
const os = require('node:os');
const { pathToFileURL } = require('node:url');

const isDev = !app.isPackaged;
const repoRoot = path.join(__dirname, '..');

let backendProc = null;
let webServer = null;
let pdfServer = null;
let mainWindow = null;
// Last auto-update event pushed to the renderer, cached so a window that loads
// (or reloads) after an event still gets the current state via `updater:state`.
let lastUpdaterEvent = { type: 'idle' };

// --- utilities -------------------------------------------------------------

function freePort() {
	return new Promise((resolve, reject) => {
		const srv = net.createServer();
		srv.unref();
		srv.on('error', reject);
		srv.listen(0, '127.0.0.1', () => {
			const { port } = srv.address();
			srv.close(() => resolve(port));
		});
	});
}

// The renderer's localStorage (theme override, queue filters, draft cart) is
// keyed to the window's origin. A random web port every launch means a new
// origin and therefore empty localStorage each run, so prefer a fixed port and
// only fall back to a free one if it's actually taken.
const PREFERRED_WEB_PORT = 43117;

function pickWebPort(preferred) {
	return new Promise((resolve) => {
		const probe = net.createServer();
		probe.once('error', () => resolve(freePort()));
		probe.listen(preferred, '127.0.0.1', () => probe.close(() => resolve(preferred)));
	});
}

// GUI apps don't inherit the login-shell PATH, so `shutil.which("claude")` in the
// backend would fail. Merge the login shell's PATH (+ common bins) into our env
// before spawning the sidecar. No-op on Windows (GUI inherits PATH there).
function resolveShellPath() {
	const common = [
		'/usr/local/bin',
		'/opt/homebrew/bin',
		'/home/linuxbrew/.linuxbrew/bin',
		path.join(os.homedir(), '.local', 'bin'),
		path.join(os.homedir(), '.bun', 'bin')
	];
	let merged = process.env.PATH || '';
	if (process.platform !== 'win32') {
		try {
			const shell = process.env.SHELL || '/bin/bash';
			const out = execSync(`${shell} -ilc 'printf %s "$PATH"'`, {
				timeout: 5000,
				encoding: 'utf8'
			});
			if (out && out.trim()) merged = out.trim();
		} catch {
			// login-shell probe failed — fall back to inherited PATH + common bins
		}
	}
	const parts = merged.split(path.delimiter);
	for (const dir of common) if (!parts.includes(dir)) parts.push(dir);
	return parts.filter(Boolean).join(path.delimiter);
}

function waitForHealth(base, timeoutMs = 30000) {
	const deadline = Date.now() + timeoutMs;
	return new Promise((resolve) => {
		const tick = () => {
			const req = http.get(`${base}/api/health`, (res) => {
				res.resume();
				if (res.statusCode === 200) return resolve(true);
				retry();
			});
			req.on('error', retry);
			req.setTimeout(1000, () => req.destroy());
		};
		const retry = () => {
			if (Date.now() > deadline) return resolve(false);
			setTimeout(tick, 250);
		};
		tick();
	});
}

// --- backend sidecar -------------------------------------------------------

function backendCommand(apiPort, env) {
	if (isDev) {
		return {
			cmd: 'uv',
			args: ['run', 'job-applier', 'serve', '--prod', '--port', String(apiPort)],
			opts: { cwd: repoRoot, env }
		};
	}
	const bin =
		process.platform === 'win32' ? 'job-applier-backend.exe' : 'job-applier-backend';
	return {
		cmd: path.join(process.resourcesPath, 'backend', bin),
		args: [],
		opts: { env }
	};
}

function startBackend(apiPort, pdfBase) {
	const env = {
		...process.env,
		PATH: resolveShellPath(),
		JOB_APPLIER_API_PORT: String(apiPort),
		// Data location precedence:
		//   1. an explicit JOB_APPLIER_DATA_DIR (e.g. a throwaway copy for testing),
		//   2. in dev, the repo's data/ — so the Electron shell, `make api/web`, and
		//      the CLI all share one database (no "why is Electron empty?" surprises),
		//   3. when packaged, the per-user app-data dir (there is no repo to point at).
		JOB_APPLIER_DATA_DIR:
			process.env.JOB_APPLIER_DATA_DIR ||
			(isDev ? path.join(repoRoot, 'data') : app.getPath('userData')),
		JOB_APPLIER_PDF_SERVICE: pdfBase
	};
	const { cmd, args, opts } = backendCommand(apiPort, env);
	// windowsHide: never flash a console window when this windowless GUI spawns
	// the sidecar. Belt-and-suspenders with the headless (console=False)
	// PyInstaller build — see desktop/sidecar/job-applier-backend.spec.
	backendProc = spawn(cmd, args, { stdio: 'inherit', windowsHide: true, ...opts });
	backendProc.on('exit', (code) => {
		if (code && code !== 0 && !app.isQuitting) {
			dialog.showErrorBox('job-applier', `Backend exited unexpectedly (code ${code}).`);
		}
	});
}

// --- SvelteKit handler (in-process, Electron's Node) -----------------------

async function startWebServer(webPort, apiBase) {
	// adapter-node reads these at import time / per request.
	process.env.JOB_APPLIER_API_BASE = apiBase;
	process.env.ORIGIN = `http://127.0.0.1:${webPort}`;
	process.env.PORT = String(webPort);

	const handlerPath = isDev
		? path.join(repoRoot, 'web', 'build', 'handler.js')
		: path.join(process.resourcesPath, 'web', 'handler.js');
	const { handler } = await import(pathToFileURL(handlerPath).href);

	webServer = http.createServer((req, res) =>
		handler(req, res, () => {
			res.statusCode = 404;
			res.end('Not found');
		})
	);
	await new Promise((resolve) => webServer.listen(webPort, '127.0.0.1', resolve));
}

// --- PDF print service (Electron printToPDF) -------------------------------

// Draft PDFs print from trusted local HTML (inline CSS, no subresources), but the
// draft text itself is derived from an untrusted job description. A prompt-injected
// draft could embed an <img>/<link> at an attacker URL to exfiltrate the resume's PII
// when the page renders. Print in an isolated session that cancels every request other
// than the top-document navigation, so no such subresource is ever fetched. Mirrors the
// Playwright guard in src/job_applier/pdf.py. JS is disabled too (the HTML needs none).
const PRINT_PARTITION = 'print-isolated';

function configurePrintSession() {
	const printSession = session.fromPartition(PRINT_PARTITION);
	printSession.webRequest.onBeforeRequest((details, callback) => {
		callback({ cancel: details.resourceType !== 'mainFrame' });
	});
	return printSession;
}

async function printUrlToPdf(url) {
	const win = new BrowserWindow({
		show: false,
		webPreferences: {
			offscreen: true,
			javascript: false,
			partition: PRINT_PARTITION,
			contextIsolation: true,
			nodeIntegration: false
		}
	});
	try {
		await win.loadURL(url);
		return await win.webContents.printToPDF({
			printBackground: true,
			preferCSSPageSize: true
		});
	} finally {
		win.destroy();
	}
}

async function startPdfService() {
	configurePrintSession();
	const port = await freePort();
	pdfServer = http.createServer((req, res) => {
		if (req.method !== 'POST' || req.url !== '/print') {
			res.statusCode = 404;
			return res.end();
		}
		let body = '';
		req.on('data', (c) => (body += c));
		req.on('end', async () => {
			try {
				const { url } = JSON.parse(body || '{}');
				const pdf = await printUrlToPdf(url);
				res.setHeader('content-type', 'application/pdf');
				res.end(pdf);
			} catch (err) {
				res.statusCode = 500;
				res.end(String(err));
			}
		});
	});
	await new Promise((resolve) => pdfServer.listen(port, '127.0.0.1', resolve));
	return `http://127.0.0.1:${port}`;
}

// --- external links --------------------------------------------------------

// The app itself is served from 127.0.0.1 (web port) with PDFs on 127.0.0.1
// (api port); everything else is a third-party URL.
function isInternalUrl(target) {
	try {
		const host = new URL(target).hostname;
		return host === '127.0.0.1' || host === 'localhost';
	} catch {
		return false;
	}
}

// Route external http(s) links — "View original posting", links inside a job
// description, doc links in Settings — to the OS default browser instead of a
// bare Electron window. Internal localhost URLs (in-app navigation, PDF
// previews/downloads) keep their normal behavior.
function registerExternalLinks(contents) {
	contents.setWindowOpenHandler(({ url }) => {
		if (/^https?:\/\//i.test(url) && !isInternalUrl(url)) {
			shell.openExternal(url);
			return { action: 'deny' };
		}
		return { action: 'allow' };
	});
	contents.on('will-navigate', (event, url) => {
		if (/^https?:\/\//i.test(url) && !isInternalUrl(url)) {
			event.preventDefault();
			shell.openExternal(url);
		}
	});
}

// --- auto-update (electron-updater) ----------------------------------------

// Two-phase, user-driven update (matches the design): check on launch, show the
// titlebar pill when a release exists, and let the user tap Download → Restart.
// electron-updater reads app-update.yml (generated from the electron-builder
// `publish` block) to find the feed and verifies the installer's sha512 against
// latest.yml. Unsigned on Windows: the NSIS installer shows a one-time SmartScreen
// the README documents. Both Linux targets self-update: AppImage swaps the file,
// and the .deb installs via dpkg behind a polkit prompt (electron-builder marks
// deb as auto-updatable and writes its entry into latest-linux.yml). A machine with
// neither dpkg nor apt is the case that errors, and it surfaces as a toast.
//
// The main process owns electron-updater and streams typed events to the renderer
// (updater:event), also caching the latest in lastUpdaterEvent so a window that
// mounts after an event can pull current state via updater:state. The renderer
// (web/src/lib/updater.svelte.ts) turns them into the pill + popover + Settings card.

const send = (type, payload = {}) => {
	lastUpdaterEvent = { type, ...payload };
	// The renderer may not have subscribed yet, and a window reload drops its
	// in-memory state — the cache covers both; it backfills via updater:state on
	// mount. webContents can be torn down independently of the window, and send()
	// on a dead one throws; swallowing here matters because one caller is itself a
	// .catch() with no downstream handler (an unhandled rejection kills the app).
	if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.webContents.isDestroyed()) {
		try {
			mainWindow.webContents.send('updater:event', lastUpdaterEvent);
		} catch {
			/* window tearing down */
		}
	}
};

const errText = (err) => String(err?.message || err);

// Trim electron-updater's UpdateInfo to just what the popover + Settings card show.
function pickInfo(info) {
	return {
		version: info?.version,
		releaseDate: info?.releaseDate,
		sizeBytes: info?.files?.[0]?.size || 0,
		notes: normalizeNotes(info?.releaseNotes)
	};
}

// releaseNotes may be a string (HTML) or an array of {version, note}; normalize to
// a plain string[] the "What's new" list can render.
function normalizeNotes(rn) {
	if (!rn) return [];
	if (Array.isArray(rn)) {
		return rn.map((r) => String((r && r.note) || '').replace(/<[^>]+>/g, '').trim()).filter(Boolean);
	}
	return String(rn)
		.replace(/<\/li>/gi, '\n')
		.replace(/<[^>]+>/g, '')
		.split('\n')
		.map((s) => s.trim())
		.filter(Boolean);
}

function initAutoUpdater() {
	// electron-updater no-ops in dev ("application is not packed", checkForUpdates
	// resolves null) because there's no app-update.yml, so the whole flow is
	// packaged-only; `make electron` dev runs just skip it.
	if (!app.isPackaged) return;

	// autoDownload=false is intentional: the UI's two-phase Download → Restart depends
	// on it. autoInstallOnAppQuit is the safety net if they quit instead of restarting.
	autoUpdater.autoDownload = false;
	autoUpdater.autoInstallOnAppQuit = true;

	autoUpdater.on('checking-for-update', () => send('checking'));
	autoUpdater.on('update-available', (info) => send('available', { info: pickInfo(info) }));
	autoUpdater.on('update-not-available', (info) => send('not-available', { info: pickInfo(info) }));
	autoUpdater.on('download-progress', (p) => send('progress', { percent: (p && p.percent) || 0 }));
	autoUpdater.on('update-downloaded', (info) => send('downloaded', { info: pickInfo(info) }));
	// Fail soft: a feed error / rate-limit / non-updatable package (.deb) must never
	// crash the app — it surfaces as an error toast and the UI recovers.
	autoUpdater.on('error', (err) => send('error', { message: errText(err) }));

	// Quiet launch check: the pill only appears on 'available', and the renderer
	// only toasts "you're on the latest version" for a check the user asked for,
	// so this never nags when up to date.
	autoUpdater.checkForUpdates().catch((err) => send('error', { message: errText(err) }));
}

// Renderer-driven controls. `state`/`check`/`download` are request/response (invoke);
// `install` is fire-and-forget (the app is about to quit). `check`/`download`/`install`
// are guarded on app.isPackaged so a browser-dev build or an unpacked run can't reach
// downloadUpdate/quitAndInstall; `state` needs no guard (it only reads the cache).
//
// Rejections are deliberately NOT swallowed here: ipcMain.handle forwards them to the
// renderer's invoke(), which is where the recovery lives (reset `downloading`, toast).
function registerUpdaterIpc() {
	ipcMain.handle('updater:state', () => lastUpdaterEvent);
	ipcMain.handle('updater:check', async () => {
		// Not packaged: no listeners are registered, so nothing would ever come back
		// and the UI would sit on "checking…" forever. Answer synthetically instead.
		if (!app.isPackaged) {
			send('error', { message: 'Updates are only available in the packaged app.' });
			return lastUpdaterEvent;
		}
		await autoUpdater.checkForUpdates();
		return lastUpdaterEvent;
	});
	ipcMain.handle('updater:download', async () => {
		if (!app.isPackaged) throw new Error('Updates are only available in the packaged app.');
		await autoUpdater.downloadUpdate();
	});
	ipcMain.on('updater:install', async () => {
		if (!app.isPackaged) return;
		// quitAndInstall spawns the installer BEFORE app.quit() runs shutdown(), so the
		// PyInstaller sidecar could still be holding a file lock on resources/backend
		// inside the install dir while NSIS tries to replace it. Stop it and wait first.
		app.isQuitting = true;
		await stopBackend();
		try {
			// isSilent=false shows the installer UI; the relaunch comes from
			// autoRunAppAfterInstall (default true), NOT from the second arg — that one
			// is only honored in silent mode.
			autoUpdater.quitAndInstall(false, true);
		} catch (err) {
			// Install refused (e.g. no dpkg/apt for a .deb). Stay alive and report it,
			// and clear isQuitting or a later backend crash would be silently swallowed.
			app.isQuitting = false;
			send('error', { message: errText(err) });
		}
	});
}

// --- lifecycle -------------------------------------------------------------

// Electron's loadURL() rejects whenever a navigation is aborted or the target
// isn't reachable yet (ERR_ABORTED, ERR_CONNECTION_REFUSED). Against the Vite
// dev server that's routine: when electronmon relaunches us after a main.js
// edit, Vite may be mid-HMR/restart for a beat. Letting that rejection escape
// boot() turns it (under Node's default --unhandled-rejections=throw) into an
// uncaught exception, which the electronmon hook latches as "errored" and then
// refuses to auto-relaunch until the next file change — i.e. the app closes on
// hot-reload and stays closed. So retry transient load failures instead.
async function loadWithRetry(win, url, { attempts = 40, delayMs = 250 } = {}) {
	for (let i = 1; ; i++) {
		try {
			await win.loadURL(url);
			return;
		} catch (err) {
			if (win.isDestroyed() || i >= attempts) throw err;
			await new Promise((resolve) => setTimeout(resolve, delayMs));
		}
	}
}

async function boot() {
	// Hot-reload dev mode (`make electron-dev`): an external orchestrator already
	// runs the backend (uvicorn --reload) and the Vite dev server, passing their
	// locations in via env. Electron then points the window at Vite for renderer
	// HMR and reuses the given API base — it does not spawn or own the backend, so
	// there's no second, unused backend fighting over the same SQLite file.
	const devUrl = process.env.JOB_APPLIER_DEV_URL;
	const externalApiBase = devUrl ? process.env.JOB_APPLIER_API_BASE : null;

	let apiBase;
	if (externalApiBase) {
		apiBase = externalApiBase;
	} else {
		const apiPort = await freePort();
		apiBase = `http://127.0.0.1:${apiPort}`;
		const pdfBase = await startPdfService();
		startBackend(apiPort, pdfBase);
	}

	const healthy = await waitForHealth(apiBase);
	if (!healthy) {
		dialog.showErrorBox('job-applier', 'Backend did not become healthy in time.');
		app.quit();
		return;
	}

	let loadUrl;
	if (devUrl) {
		loadUrl = devUrl;
	} else {
		const webPort = await pickWebPort(PREFERRED_WEB_PORT);
		await startWebServer(webPort, apiBase);
		loadUrl = `http://127.0.0.1:${webPort}`;
	}

	// Surface the stamped app version (electron reads it from package.json, which
	// `make stamp-version` / the release workflow write from the backend
	// __version__) to the preload bridge. Set before the window spawns so the
	// renderer process inherits it. `npm_package_version` is only present under
	// `npm start`, not in a packaged app, so it can't be relied on there.
	process.env.JOB_APPLIER_APP_VERSION = app.getVersion();

	mainWindow = new BrowserWindow({
		width: 1280,
		height: 860,
		minWidth: 940,
		minHeight: 600,
		// Frameless: the redesigned SvelteKit titlebar draws the brand, command
		// search, theme toggle, and window controls (Phase 8). The renderer routes
		// min/max/close back over IPC (see registerWindowIpc + preload.js).
		frame: false,
		backgroundColor: '#16181d',
		webPreferences: { preload: path.join(__dirname, 'preload.js') }
	});
	registerExternalLinks(mainWindow.webContents);
	await loadWithRetry(mainWindow, loadUrl);

	// Kick off the update check once the window is up so its events have somewhere
	// to land. Packaged-only (see initAutoUpdater); a no-op in dev.
	initAutoUpdater();
}

// Window controls invoked from the custom titlebar. Toggle maximize so the
// titlebar's maximize button also restores.
function registerWindowIpc() {
	ipcMain.on('window:minimize', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
	ipcMain.on('window:maximize', (e) => {
		const win = BrowserWindow.fromWebContents(e.sender);
		if (!win) return;
		if (win.isMaximized()) win.unmaximize();
		else win.maximize();
	});
	ipcMain.on('window:close', (e) => BrowserWindow.fromWebContents(e.sender)?.close());
}

// SIGTERM the sidecar and resolve once it's actually gone (SIGKILL, then give up,
// after `graceMs`). The updater path awaits this before handing off to the installer:
// the sidecar's exe lives inside the install directory, and Windows won't let NSIS
// replace a running image.
function stopBackend(graceMs = 4000) {
	if (!backendProc || backendProc.exitCode !== null) return Promise.resolve();
	const proc = backendProc;
	return new Promise((resolve) => {
		const done = () => {
			clearTimeout(hard);
			clearTimeout(giveUp);
			resolve();
		};
		proc.once('exit', done);
		proc.kill('SIGTERM');
		const hard = setTimeout(() => {
			if (proc.exitCode === null) proc.kill('SIGKILL');
		}, graceMs);
		// Never block quitting forever if the process refuses to die.
		const giveUp = setTimeout(done, graceMs + 1000);
	});
}

function shutdown() {
	app.isQuitting = true;
	// Fire-and-forget here: 'before-quit' is synchronous, so we can't await. The
	// updater path calls stopBackend() directly and does await it.
	void stopBackend();
	try {
		webServer?.close();
	} catch {
		/* ignore */
	}
	try {
		pdfServer?.close();
	} catch {
		/* ignore */
	}
}

registerWindowIpc();
registerUpdaterIpc();
// Guard the whole boot chain: a rejection here (failed dev-server load, web
// handler import, etc.) must not surface as an uncaught exception, or the
// electronmon dev hook latches "errored" and stops auto-relaunching after a
// hot reload. Fail loudly and quit instead of dying silently mid-restart.
app.whenReady()
	.then(boot)
	.catch((err) => {
		dialog.showErrorBox('job-applier', `Startup failed:\n${err?.stack || err}`);
		app.quit();
	});
app.on('before-quit', shutdown);
app.on('window-all-closed', () => {
	shutdown();
	app.quit();
});
