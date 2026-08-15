// Dev Electron launcher for `make electron-dev` (invoked as the `dev:app` slot).
//
// Exists for one reason: to delete ELECTRON_RUN_AS_NODE before Electron starts.
//
// That variable is how Electron's own binary is reused as a plain Node runtime,
// and Electron-based editors export it to their child processes — VS Code's
// integrated terminal is the common case, so `make electron-dev` crashes there
// while working fine from a stock terminal. When it's set, `electron .` boots as
// bare Node: no GUI, and `require('electron')` returns the *path string* to the
// binary instead of the API object. Every `.app` lookup then reads as undefined,
// and the first one to run wins the crash — today that's electron-updater's
// eager `autoUpdater` getter at the top of main.js:
//
//     TypeError: Cannot read properties of undefined (reading 'getVersion')
//
// electronmon's uncaughtException handler hits the same undefined `.app` while
// trying to report it, so the original error is swallowed and you get a bare
// exit code instead of a cause. Hence clearing it here, before anything loads.
//
// It has to be a real `delete`: an empty value still counts as set to Electron,
// so `cross-env ELECTRON_RUN_AS_NODE= ...` does not work.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = fileURLToPath(new URL('.', import.meta.url));

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
// Kept in sync with the packaged app's expectations; see main.js.
env.JOB_APPLIER_DEV_URL = env.JOB_APPLIER_DEV_URL || 'http://127.0.0.1:5174';
env.JOB_APPLIER_API_BASE = env.JOB_APPLIER_API_BASE || 'http://127.0.0.1:8000';

// Run electronmon's CLI directly rather than through the .bin shim so there's no
// shell quoting to get wrong on Windows.
const child = spawn(process.execPath, [require.resolve('electronmon/bin/cli.js'), '.'], {
	cwd: here,
	stdio: 'inherit',
	env
});

const stop = () => {
	if (child.exitCode === null) child.kill();
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
child.on('exit', (code) => process.exit(code ?? 0));
