<script lang="ts">
	import Icon from '$lib/Icon.svelte';

	let { open = false, onClose, mod = 'Ctrl' }: { open?: boolean; onClose: () => void; mod?: string } =
		$props();

	const shortcuts = $derived<[string, string[]][]>([
		['Command palette', [mod, 'K']],
		['Dashboard / Queue / …', [mod, '1–6']],
		['Next / previous job', ['J', 'K']],
		['Search / commands', ['/']],
		['Add to draft list', [mod, 'D']],
		['Toggle theme', [mod, 'J']],
		['Keyboard shortcuts', ['?']],
		['Close overlay', ['Esc']]
	]);
</script>

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div class="scrim" onclick={(e) => e.target === e.currentTarget && onClose()}>
		<div class="sheet" role="dialog" aria-label="Keyboard shortcuts" aria-modal="true">
			<div class="sheet-h">
				<h3>Keyboard shortcuts</h3>
				<button class="icon-btn" onclick={onClose} aria-label="Close"><Icon name="x" size={16} stroke={2} /></button>
			</div>
			<div class="sheet-b">
				{#each shortcuts as [label, keys] (label)}
					<div class="sc-row">
						<span class="sc-l">{label}</span>
						<span class="sc-keys">{#each keys as k (k)}<kbd>{k}</kbd>{/each}</span>
					</div>
				{/each}
			</div>
		</div>
	</div>
{/if}
