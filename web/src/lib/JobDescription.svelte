<script lang="ts">
	// Renders scraped job-description HTML, normalized into the theme via the
	// shared `.jd-prose` rules in app.css. Centralizes the one `{@html}` sink for
	// untrusted markup so both detail views (queue pane + /jobs/[id]) stay identical.
	let { html }: { html: string | null | undefined } = $props();
</script>

<div class="jd-prose">
	<!-- eslint-disable-next-line svelte/no-at-html-tags -->
	{@html html ?? ''}
</div>
