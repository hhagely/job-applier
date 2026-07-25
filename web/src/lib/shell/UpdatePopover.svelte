<script lang="ts">
	import { updater } from '$lib/updater.svelte';

	// Fall back to a generic line when the release carries no notes (electron-updater
	// only populates releaseNotes when the release body is present).
	let notes = $derived(updater.notes.length ? updater.notes : ['Bug fixes and improvements.']);
	let label = $derived(
		updater.downloaded ? 'Restart to install' : updater.downloading ? 'Downloading…' : 'Download & install'
	);
	// Built as one expression rather than markup: whitespace around a Svelte {#if}
	// boundary gets collapsed, which ate the space before the separator.
	let sizeSuffix = $derived(updater.size ? ` · ${updater.size}` : '');
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="up-scrim" class:show={updater.popoverOpen} id="upScrim" onclick={() => updater.closePopover()}></div>

<div class="up-pop" class:show={updater.popoverOpen} id="upPop" role="dialog" aria-label="Software update">
	<div class="uh">
		<span class="ui">
			<svg
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M5 21h14" />
			</svg>
		</span>
		<div>
			<div class="uh-t">Update available</div>
			<div class="uh-s">
				job-applier <b id="upVer" style="color:var(--fg)">v{updater.version}</b>{sizeSuffix}
			</div>
		</div>
	</div>
	<div class="ub">
		<h4>What's new</h4>
		<ul class="un" id="upNotes">
			{#each notes as n (n)}<li>{n}</li>{/each}
		</ul>
	</div>
	<div class="up-prog" id="upProg" style:display={updater.downloading ? 'block' : 'none'}>
		<span style:width="{updater.percent}%"></span>
	</div>
	<div class="uf">
		<a
			class="lnk"
			id="upNotesLink"
			href={updater.releaseUrl}
			target="_blank"
			rel="noopener"
			onclick={() => updater.closePopover()}>Full release notes</a
		>
		<button type="button" class="btn sm" id="upLater" onclick={() => updater.closePopover()}>Later</button>
		<button
			type="button"
			class="btn primary sm"
			id="upInstall"
			onclick={() => updater.install()}
			disabled={updater.downloading}
		>
			{label}
		</button>
	</div>
</div>

<style>
	.up-scrim {
		position: fixed;
		inset: 0;
		z-index: 110;
		display: none;
	}
	.up-scrim.show {
		display: block;
	}
	.up-pop {
		position: fixed;
		top: 46px;
		right: 14px;
		width: 334px;
		z-index: 120;
		display: none;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 13px;
		overflow: hidden;
		box-shadow:
			0 18px 44px -14px rgba(0, 0, 0, 0.55),
			0 0 0 1px var(--border);
	}
	.up-pop.show {
		display: block;
		animation: uppop 0.16s ease;
	}
	@keyframes uppop {
		from {
			opacity: 0;
			transform: translateY(-6px);
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	.uh {
		display: flex;
		align-items: center;
		gap: 11px;
		padding: 14px 15px 10px;
	}
	.uh .ui {
		width: 34px;
		height: 34px;
		border-radius: 9px;
		flex: none;
		display: grid;
		place-items: center;
		color: var(--accent);
		background: var(--accent-soft);
	}
	.uh .ui svg {
		width: 18px;
		height: 18px;
	}
	.uh-t {
		font-weight: 650;
		font-size: 13.5px;
	}
	.uh-s {
		font-size: 11.5px;
		color: var(--faint);
		margin-top: 2px;
	}
	.ub {
		padding: 2px 15px 4px;
	}
	.ub h4 {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		margin: 6px 0 7px;
		font-weight: 640;
	}
	.un {
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 7px;
		margin: 0;
		padding: 0;
	}
	.un li {
		position: relative;
		padding-left: 15px;
		font-size: 12.5px;
		color: var(--muted);
		line-height: 1.42;
	}
	.un li::before {
		content: '';
		position: absolute;
		left: 2px;
		top: 6px;
		width: 5px;
		height: 5px;
		border-radius: 50%;
		background: var(--strong);
	}
	.up-prog {
		height: 5px;
		border-radius: 3px;
		background: var(--surface-2);
		overflow: hidden;
		margin: 9px 15px 0;
	}
	.up-prog > span {
		display: block;
		height: 100%;
		width: 0;
		background: var(--accent);
		border-radius: 3px;
		transition: width 0.18s linear;
	}
	.uf {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 15px;
		border-top: 1px solid var(--border);
		margin-top: 9px;
	}
	.uf .lnk {
		margin-right: auto;
		font-size: 12px;
		color: var(--accent);
		cursor: pointer;
	}
	.uf .lnk:hover {
		text-decoration: underline;
	}
</style>
