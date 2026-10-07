<script lang="ts">
	// "Whose view is this": the active profile's name plus a separator, for a
	// page subtitle. Renders nothing on a single-profile install (profileLabel).
	import type { SearchProfile } from '$lib/api';
	import { profileLabel } from '$lib/profiles';

	let { profiles }: { profiles: readonly SearchProfile[] | undefined } = $props();
	const whose = $derived(profileLabel(profiles));
</script>

{#if whose}<b>{whose}</b>{' · '}{/if}
