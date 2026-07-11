// Electron main process for the job-applier desktop shell (Phase 6).
//
// Replaces the Phase 1 `app-dev` Python launcher: pick free ports, spawn the
// backend (from source in dev, the PyInstaller sidecar when packaged), run the
// SvelteKit adapter-node handler in-process, health-check the backend, then show
// the window. PDFs render via Electron's printToPDF (an offscreen window), so the
// packaged app ships no Playwright.

const { app, BrowserWindow, dialog, ipcMain, session, shell } = require('electron');
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

function shutdown() {
	app.isQuitting = true;
	if (backendProc && backendProc.exitCode === null) {
		backendProc.kill('SIGTERM');
		setTimeout(() => {
			if (backendProc && backendProc.exitCode === null) backendProc.kill('SIGKILL');
		}, 5000);
	}
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
