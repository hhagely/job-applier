import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { Score } from '$lib/api';
import ScoreBreakdown from '../ScoreBreakdown.svelte';

// Mirrors what the scoring prompts actually emit: each bucket is an object with
// `points` + a one-line `note` (see src/job_applier/ai/prompts/score.md).
function score(overrides: Partial<Score> = {}): Score {
	return {
		score: 82,
		rubric: {
			skills_overlap: { points: 26, note: 'TS/React strong; Rust light' },
			experience_match: { points: 22, note: 'Staff-level scope matches' }
		},
		reasoning: 'Strong overlap.',
		scored_by: 'claude-cli',
		scored_at: '2026-04-02T21:00:55Z',
		resume_id: 1,
		resume_filename: 'resume.pdf',
		score_kind: 'baseline',
		is_stale: false,
		...overrides
	};
}

describe('ScoreBreakdown', () => {
	it('renders the score hero, rationale, resume filename, and rubric labels', () => {
		render(ScoreBreakdown, { props: { score: score() } });
		expect(screen.getByText('82')).toBeInTheDocument();
		expect(screen.getByText('Strong overlap.')).toBeInTheDocument();
		expect(screen.getByText('resume.pdf')).toBeInTheDocument();
		expect(screen.getByText('skills_overlap')).toBeInTheDocument();
		expect(screen.getByText('experience_match')).toBeInTheDocument();
	});

	it('renders a bar, the points, and the note for each `{points, note}` bucket', () => {
		const { container } = render(ScoreBreakdown, { props: { score: score() } });
		expect(container.querySelectorAll('.meter')).toHaveLength(2);
		expect(screen.getByText('26')).toBeInTheDocument();
		expect(screen.getByText('TS/React strong; Rust light')).toBeInTheDocument();
		expect(screen.getByText('Staff-level scope matches')).toBeInTheDocument();
		expect(screen.queryByText(/"points"/)).not.toBeInTheDocument();
	});

	it('still renders a bar for the legacy bare-number bucket shape', () => {
		const { container } = render(ScoreBreakdown, {
			props: { score: score({ rubric: { skills_overlap: 26 } }) }
		});
		expect(container.querySelectorAll('.meter')).toHaveLength(1);
		expect(screen.getByText('26')).toBeInTheDocument();
	});

	it('falls back to the raw value for an unexpected bucket shape', () => {
		const { container } = render(ScoreBreakdown, {
			props: { score: score({ rubric: { skills_overlap: { score: 'high' } } }) }
		});
		expect(container.querySelector('.meter')).toBeNull();
		expect(screen.getByText('skills_overlap')).toBeInTheDocument();
		expect(screen.getByText('{"score":"high"}')).toBeInTheDocument();
	});

	it('shows the stale banner only when the score is stale', () => {
		const { unmount } = render(ScoreBreakdown, { props: { score: score({ is_stale: false }) } });
		expect(screen.queryByText(/older resume/)).not.toBeInTheDocument();
		unmount();
		render(ScoreBreakdown, { props: { score: score({ is_stale: true }) } });
		expect(screen.getByText(/older resume/)).toBeInTheDocument();
	});

	it('renders nothing when unscored', () => {
		const { container } = render(ScoreBreakdown, { props: { score: null } });
		expect(container.textContent?.trim()).toBe('');
	});
});
