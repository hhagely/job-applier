import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

import StatusBadge from './StatusBadge.svelte';

describe('StatusBadge (example component test)', () => {
	it('renders the status label', () => {
		render(StatusBadge, { status: 'applied' });
		expect(screen.getByRole('button')).toHaveTextContent('applied');
	});

	it('fires onSelect with the status when clicked', async () => {
		const onSelect = vi.fn();
		render(StatusBadge, { status: 'interested', onSelect });

		await userEvent.click(screen.getByRole('button'));
		expect(onSelect).toHaveBeenCalledExactlyOnceWith('interested');
	});
});
