import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { Score } from '$lib/api';
import ScoreBreakdown from '../ScoreBreakdown.svelte';

function score(overrides: Partial<Score> = {}): Score {
	return {
		score: 82,
		rubric: { skills_overlap: 26, experience_match: 22 },
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
