// Minimal, safe bridge into the renderer. Kept tiny on purpose — the UI is the
// same SvelteKit app. The redesign phase (Phase 8) expands this with window
// controls, theme get/set, tray, and notifications.
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
	version: process.env.npm_package_version || '0.0.0',
	platform: process.platform
});
