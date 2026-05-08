<script lang="ts">
	import type { ApplicationStatus } from '../api';

	let { status, onSelect }: { status: ApplicationStatus; onSelect?: (s: ApplicationStatus) => void } =
		$props();
</script>

<button type="button" data-status={status} onclick={() => onSelect?.(status)}>
	{status}
</button>
