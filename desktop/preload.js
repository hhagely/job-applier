// Minimal, safe bridge into the renderer. Kept tiny on purpose — the UI is the
// same SvelteKit app. Phase 8 adds `isElectron` (so the web UI shows the custom
// titlebar's window controls only inside the shell) and the window controls
// themselves, routed to the main process over IPC.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
	isElectron: true,
	version: process.env.npm_package_version || '0.0.0',
	platform: process.platform,
	windowControls: {
		minimize: () => ipcRenderer.send('window:minimize'),
		maximize: () => ipcRenderer.send('window:maximize'),
		close: () => ipcRenderer.send('window:close')
	}
});
