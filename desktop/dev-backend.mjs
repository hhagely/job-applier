// Dev backend launcher for `make electron-dev` (invoked as the `dev:api` slot).
//
// Reuse a backend already listening on :8000 (e.g. a `make api` you left
// running, or a still-warm one from a previous run) instead of starting a
// second one that would fail to bind and — under `concurrently -k` — tear down
// the whole group so Electron never launches. If nothing is there, start
// `uvicorn --reload` ourselves so Python changes still hot-reload.
//
// We probe GET /api/health, not just the raw TCP port: a wedged uvicorn (one
// that jammed mid-reload, say) keeps the port open but stops answering HTTP. A
// bare TCP probe "reuses" that corpse, and Electron then times out its own
// health check ~30s later with a vague "Backend did not become healthy" dialog.
// So if the port is open but never goes healthy, fail loudly here — at the real
// source — instead.
import net from 'node:net';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HOST = '127.0.0.1';
const PORT = 8000;
const repoRoot = fileURLToPath(new URL('..', import.meta.url));

function portIsOpen() {
	return new Promise((resolve) => {
		const sock = net.connect(PORT, HOST);
		sock.once('connect', () => {
			sock.destroy();
			resolve(true);
		});
		sock.once('error', () => resolve(false));
		sock.setTimeout(1000, () => {
			sock.destroy();
			resolve(false);
		});
	});
}

function healthOnce() {
	return new Promise((resolve) => {
		const req = http.get(`http://${HOST}:${PORT}/api/health`, (res) => {
			res.resume();
			resolve(res.statusCode === 200);
		});
		req.on('error', () => resolve(false));
		req.setTimeout(1000, () => req.destroy());
	});
}

// An already-running backend answers on the first probe; give a still-starting
// one a little slack before declaring it wedged.
async function waitHealthy(timeoutMs) {
	const deadline = Date.now() + timeoutMs;
	for (;;) {
		if (await healthOnce()) return true;
		if (Date.now() >= deadline) return false;
		await new Promise((r) => setTimeout(r, 500));
	}
}

function startUvicorn() {
	console.log(`[dev-backend] no backend on :${PORT}; starting uvicorn --reload`);
	const child = spawn(
		'uv',
		['run', 'uvicorn', 'job_applier.api.app:app', '--reload', '--port', String(PORT)],
		{ cwd: repoRoot, stdio: 'inherit', shell: process.platform === 'win32' }
	);
	const stop = () => {
		if (child.exitCode !== null) return;
		// uvicorn --reload runs a reloader that spawns a multiprocessing worker,
		// and it's that worker (not `uv`/the reloader) which holds :8000. A plain
		// child.kill() on Windows leaves the worker orphaned on the port, wedging
		// the next `make electron-dev`. Take down the whole tree instead.
		if (process.platform === 'win32') {
			try {
				spawn('taskkill', ['/pid', String(child.pid), '/T', '/F']);
			} catch {
				child.kill();
			}
		} else {
			child.kill();
		}
	};
	process.on('SIGINT', stop);
	process.on('SIGTERM', stop);
	child.on('exit', (code) => process.exit(code ?? 0));
}

if (!(await portIsOpen())) {
	startUvicorn();
} else if (await waitHealthy(15000)) {
	console.log(`[dev-backend] reusing healthy backend already on ${HOST}:${PORT}`);
	// Stay resident so `concurrently -k` doesn't read an exit here as "a process
	// died" and kill the Vite + Electron slots. Nothing to manage — just idle.
	setInterval(() => {}, 1 << 30);
} else {
	const killHint =
		process.platform === 'win32'
			? `Get-NetTCPConnection -LocalPort ${PORT} -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
			: `lsof -ti tcp:${PORT} | xargs kill`;
	console.error(
		`[dev-backend] :${PORT} is open but not answering /api/health — likely a wedged\n` +
			`backend from a previous run. Can't bind a fresh one to a busy port. Kill it\n` +
			`and re-run 'make electron-dev':\n\n    ${killHint}\n`
	);
	// Exit non-zero so `concurrently -k` tears the group down now, with this
	// message visible, instead of Electron timing out 30s later on a stale corpse.
	process.exit(1);
}
