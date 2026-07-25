// Minimal, safe bridge into the renderer. Kept tiny on purpose — the UI is the
// same SvelteKit app. Phase 8 adds `isElectron` (so the web UI shows the custom
// titlebar's window controls only inside the shell) and the window controls
// themselves, routed to the main process over IPC.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
	isElectron: true,
	// Stamped app version, set by the main process from app.getVersion() before the
	// window spawns (see main.js). Falls back to npm_package_version under `npm start`.
	version: process.env.JOB_APPLIER_APP_VERSION || process.env.npm_package_version || '0.0.0',
	platform: process.platform,
	windowControls: {
		minimize: () => ipcRenderer.send('window:minimize'),
		maximize: () => ipcRenderer.send('window:maximize'),
		close: () => ipcRenderer.send('window:close')
	},
	// Auto-update bridge (electron-updater lives in the main process). `onEvent`
	// streams typed events (checking/available/not-available/progress/downloaded/
	// error) and returns an unsubscribe fn; `getState` pulls the last event for a
	// late mount; `check` re-runs the check; `download` starts the two-phase
	// download; `install` quits and applies a downloaded update.
	updater: {
		onEvent: (cb) => {
			const listener = (_e, payload) => cb(payload);
			ipcRenderer.on('updater:event', listener);
			return () => ipcRenderer.removeListener('updater:event', listener);
		},
		getState: () => ipcRenderer.invoke('updater:state'),
		check: () => ipcRenderer.invoke('updater:check'),
		download: () => ipcRenderer.invoke('updater:download'),
		install: () => ipcRenderer.send('updater:install')
	}
});
