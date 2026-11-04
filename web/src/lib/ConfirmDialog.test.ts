import { fireEvent, render, screen } from '@testing-library/svelte';
import { createRawSnippet, tick } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ConfirmDialog from './ConfirmDialog.svelte';

const body = createRawSnippet(() => ({ render: () => '<span>Jobs are kept.</span>' }));

function setup(open = true) {
	const onConfirm = vi.fn();
	const onCancel = vi.fn();
	const view = render(ConfirmDialog, {
		props: { open, title: 'Delete "Sam"?', confirmLabel: 'Delete', danger: true, onConfirm, onCancel, children: body }
	});
	return { onConfirm, onCancel, ...view };
}

describe('ConfirmDialog', () => {
	afterEach(() => {
		document.body.innerHTML = '';
	});

	it('renders nothing while closed', () => {
		setup(false);
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('is a labelled modal described by its body', () => {
		setup();
		const dialog = screen.getByRole('dialog', { name: 'Delete "Sam"?' });
		expect(dialog).toHaveAttribute('aria-modal', 'true');
		expect(dialog).toHaveAccessibleDescription('Jobs are kept.');
	});

	it('marks the confirm button destructive when danger is set', () => {
		setup();
		expect(screen.getByRole('button', { name: 'Delete' })).toHaveClass('danger');
	});

	it('runs onConfirm from the confirm button', async () => {
		const { onConfirm, onCancel } = setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		expect(onConfirm).toHaveBeenCalledOnce();
		expect(onCancel).not.toHaveBeenCalled();
	});

	it('cancels from the Cancel button, Escape, and the scrim, never confirming', async () => {
		const { onConfirm, onCancel } = setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await fireEvent.keyDown(window, { key: 'Escape' });
		await fireEvent.click(screen.getByRole('dialog').parentElement as HTMLElement);
		expect(onCancel).toHaveBeenCalledTimes(3);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('does not cancel on a click inside the dialog', async () => {
		const { onCancel } = setup();
		await fireEvent.click(screen.getByRole('dialog'));
		expect(onCancel).not.toHaveBeenCalled();
	});

	it('starts focus on Cancel and returns it to the trigger on close', async () => {
		const trigger = document.createElement('button');
		document.body.append(trigger);
		trigger.focus();

		const { rerender } = setup(false);
		await rerender({ open: true });
		await tick();
		expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();

		await rerender({ open: false });
		await tick();
		expect(trigger).toHaveFocus();
	});

	it('keeps Tab inside the dialog', async () => {
		setup();
		await tick();
		const cancel = screen.getByRole('button', { name: 'Cancel' });
		const confirm = screen.getByRole('button', { name: 'Delete' });
		await fireEvent.keyDown(cancel, { key: 'Tab' });
		expect(confirm).toHaveFocus();
		await fireEvent.keyDown(confirm, { key: 'Tab' });
		expect(cancel).toHaveFocus();
		await fireEvent.keyDown(cancel, { key: 'Tab', shiftKey: true });
		expect(confirm).toHaveFocus();
	});

	it('hides key presses from the page only while open', async () => {
		const pageShortcut = vi.fn();
		window.addEventListener('keydown', pageShortcut);
		try {
			const { rerender } = setup(true);
			await fireEvent.keyDown(document.body, { key: 'j' });
			expect(pageShortcut).not.toHaveBeenCalled();

			await rerender({ open: false });
			await fireEvent.keyDown(document.body, { key: 'j' });
			expect(pageShortcut).toHaveBeenCalledOnce();
		} finally {
			window.removeEventListener('keydown', pageShortcut);
		}
	});
});
