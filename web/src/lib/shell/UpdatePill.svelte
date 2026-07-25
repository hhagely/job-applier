<script lang="ts">
	import { updater } from '$lib/updater.svelte';
</script>

<!-- Titlebar update pill (design id #updatePill). Hidden until `.show`; clicking
	 toggles the update popover. Only ever shows inside the desktop shell. -->
<button
	class="update-pill"
	class:show={updater.available}
	id="updatePill"
	title={'Update available — v' + updater.version}
	aria-label={'Update available — v' + updater.version}
	onclick={() => updater.togglePopover()}
>
	<span class="ud-dot"></span>
	<svg
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2.2"
		stroke-linecap="round"
		stroke-linejoin="round"
	>
		<path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M5 21h14" />
	</svg>
	Update
</button>
