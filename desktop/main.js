// Electron main process for the job-applier desktop shell (Phase 6).
//
// Replaces the Phase 1 `app-dev` Python launcher: pick free ports, spawn the
// backend (from source in dev, the PyInstaller sidecar when packaged), run the
// SvelteKit adapter-node handler in-process, health-check the backend, then show
// the window. PDFs render via Electron's printToPDF (an offscreen window), so the
// packaged app ships no Playwright.

const { app, BrowserWindow, dialog } = require('electron');
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
		JOB_APPLIER_DATA_DIR: app.getPath('userData'),
		JOB_APPLIER_PDF_SERVICE: pdfBase
	};
	const { cmd, args, opts } = backendCommand(apiPort, env);
	backendProc = spawn(cmd, args, { stdio: 'inherit', ...opts });
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

async function printUrlToPdf(url) {
	const win = new BrowserWindow({
		show: false,
		webPreferences: { offscreen: true, javascript: true }
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

// --- lifecycle -------------------------------------------------------------

async function boot() {
	const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
	const apiBase = `http://127.0.0.1:${apiPort}`;

	const pdfBase = await startPdfService();
	startBackend(apiPort, pdfBase);

	const healthy = await waitForHealth(apiBase);
	if (!healthy) {
		dialog.showErrorBox('job-applier', 'Backend did not become healthy in time.');
		app.quit();
		return;
	}

	// A power-user dev override: point at the Vite dev server for UI hot-reload.
	const devUrl = process.env.JOB_APPLIER_DEV_URL;
	let loadUrl;
	if (devUrl) {
		loadUrl = devUrl;
	} else {
		await startWebServer(webPort, apiBase);
		loadUrl = `http://127.0.0.1:${webPort}`;
	}

	mainWindow = new BrowserWindow({
		width: 1280,
		height: 860,
		backgroundColor: '#0e1116',
		webPreferences: { preload: path.join(__dirname, 'preload.js') }
	});
	await mainWindow.loadURL(loadUrl);
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

app.whenReady().then(boot);
app.on('before-quit', shutdown);
app.on('window-all-closed', () => {
	shutdown();
	app.quit();
});
