import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { TaskSnapshot } from '$lib/api';
import ScoreProgress from '../ScoreProgress.svelte';

function task(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
	return {
		id: 't1',
		kind: 'score_pending',
		total: 4,
		done: 2,
		status: 'running',
		errors: [],
		results: [],
		...overrides
	};
}

describe('ScoreProgress', () => {
	it('renders running progress and hides Dismiss while running', () => {
		render(ScoreProgress, { props: { task: task(), onDismiss: () => {} } });
		expect(screen.getByText(/Scoring 2\/4/)).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
	});

	it('shows completion summary and per-job results toggle', async () => {
		render(ScoreProgress, {
			props: {
				task: task({
					status: 'done',
					done: 4,
					results: ['1  82/100  Senior Engineer', '2  40/100  Junior Dev']
				}),
				onDismiss: () => {}
			}
		});
		expect(screen.getByText(/Scored 4\/4/)).toBeInTheDocument();

		// Results are collapsed until toggled.
		expect(screen.queryByText(/82\/100/)).not.toBeInTheDocument();
		await userEvent.click(screen.getByRole('button', { name: /per-job results/ }));
		expect(await screen.findByText(/82\/100\s+Senior Engineer/)).toBeInTheDocument();
	});

	it('surfaces error count + list and fires onDismiss', async () => {
		const onDismiss = vi.fn();
		render(ScoreProgress, {
			props: {
				task: task({ status: 'done', done: 4, errors: ['7: invalid JSON after retry'] }),
				onDismiss
			}
		});
		expect(screen.getByText('1 error')).toBeInTheDocument();
		expect(screen.getByText('7: invalid JSON after retry')).toBeInTheDocument();
		await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
		expect(onDismiss).toHaveBeenCalledOnce();
	});
});
