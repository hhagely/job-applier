<script lang="ts" module>
	// Stroke-based 24x24 icon set (Feather-ish), matching the redesign mockup.
	// Inner markup only; the <svg> wrapper carries the shared stroke attrs.
	export const ICONS: Record<string, string> = {
		dashboard:
			'<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
		queue:
			'<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1.3"/><circle cx="3.5" cy="12" r="1.3"/><circle cx="3.5" cy="18" r="1.3"/>',
		clock: '<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>',
		doc: '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
		funnel: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
		gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-2.7 1.1V21a2 2 0 01-4 0v-.1A1.6 1.6 0 004 19.4l-.1.1a2 2 0 11-2.8-2.8l.1-.1A1.6 1.6 0 002.6 14H3a2 2 0 010-4h.1A1.6 1.6 0 004.6 8.3L4.5 8.2A2 2 0 117.3 5.4l.1.1A1.6 1.6 0 0010 5.6V5a2 2 0 014 0v.1a1.6 1.6 0 002.7 1.1l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8v.1"/>',
		refresh: '<path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
		plus: '<path d="M12 5v14M5 12h14"/>',
		search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
		moon: '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>',
		sun: '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
		help: '<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 015.8 1c0 2-3 2.5-3 4"/><path d="M12 17h.01"/>',
		key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l7-7 3 3M15 6l2 2"/>',
		star: '<path d="M12 2l2.4 7.4H22l-6 4.5 2.3 7.1L12 16.8 5.7 21l2.3-7.1-6-4.5h7.6z"/>',
		check: '<path d="M20 6L9 17l-5-5"/>',
		chart: '<path d="M3 3v18h18"/><path d="M7 14l3-4 3 3 4-6"/>',
		flag: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 4.3L2 18a2 2 0 001.7 3h16.6a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z"/>',
		external: '<path d="M7 17L17 7M9 7h8v8"/>',
		chevron: '<path d="M9 6l6 6-6 6"/>',
		upload: '<path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 20h16"/>',
		palette: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 9l3 3-3 3"/>',
		logo: '<path d="M20 6L9 17l-5-5"/>',
		x: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>'
	};
</script>

<script lang="ts">
	let {
		name,
		size = 17,
		stroke = 1.9
	}: { name: keyof typeof ICONS | string; size?: number; stroke?: number } = $props();
</script>

<svg
	viewBox="0 0 24 24"
	width={size}
	height={size}
	fill="none"
	stroke="currentColor"
	stroke-width={stroke}
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	{@html ICONS[name] ?? ''}
</svg>
