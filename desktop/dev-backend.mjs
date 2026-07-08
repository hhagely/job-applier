// Dev backend launcher for `make electron-dev` (invoked as the `dev:api` slot).
//
// Reuse a backend already listening on :8000 (e.g. a `make api` you left
// running, or an orphaned uvicorn from a previous run) instead of starting a
// second one that would fail to bind and — under `concurrently -k` — tear down
// the whole group so Electron never launches. If nothing is there, start
// `uvicorn --reload` ourselves so Python changes still hot-reload.
import net from 'node:net';
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

if (await portIsOpen()) {
	console.log(`[dev-backend] reusing backend already on ${HOST}:${PORT}`);
	// Stay resident so `concurrently -k` doesn't read an exit here as "a process
	// died" and kill the Vite + Electron slots. Nothing to manage — just idle.
	setInterval(() => {}, 1 << 30);
} else {
	console.log(`[dev-backend] no backend on :${PORT}; starting uvicorn --reload`);
	const child = spawn(
		'uv',
		['run', 'uvicorn', 'job_applier.api.app:app', '--reload', '--port', String(PORT)],
		{ cwd: repoRoot, stdio: 'inherit', shell: process.platform === 'win32' }
	);
	const stop = () => {
		if (child.exitCode === null) child.kill();
	};
	process.on('SIGINT', stop);
	process.on('SIGTERM', stop);
	child.on('exit', (code) => process.exit(code ?? 0));
}
