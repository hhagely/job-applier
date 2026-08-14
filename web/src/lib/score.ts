// Semantic match-score scale, shared by every place a score renders (queue
// rows, the detail pane, the dashboard). Thresholds come from the brand spec:
//   strong >= 80  (green) · good 65-79 (amber) · weak < 65 (rose)
// `none` covers unscored jobs.

export type ScoreBand = 'strong' | 'good' | 'weak' | 'none';

export function scoreBand(score: number | null | undefined): ScoreBand {
	if (score == null) return 'none';
	if (score >= 80) return 'strong';
	if (score >= 65) return 'good';
	return 'weak';
}

/**
 * Max points per rubric bucket.
 *
 * SYNC: these are the bucket weights in `src/job_applier/ai/prompts/score.md` +
 * `score_batch.md` (they sum to 100). A bucket is scored out of *its own* weight,
 * never out of 100 — so a bar or band computed from raw points reads a perfect
 * 10/10 `hard_requirements` as 10%, i.e. "weak". Always divide by the weight first.
 */
export const RUBRIC_WEIGHTS: Record<string, number> = {
	skills_overlap: 30,
	experience_match: 25,
	role_fit: 20,
	domain_fit: 15,
	hard_requirements: 10
};

/**
 * The bucket's max. Unknown keys (a future prompt growing a bucket this list hasn't
 * learned yet) fall back to 100, which is the only honest denominator when we don't
 * know the weight — the bar under-reads rather than silently over-reading.
 */
export function rubricWeight(bucket: string): number {
	return RUBRIC_WEIGHTS[bucket] ?? 100;
}

/** A bucket's fill as a 0-100 percentage of its own weight. */
export function rubricPercent(points: number, weight: number): number {
	if (!(weight > 0)) return 0;
	return Math.max(0, Math.min(100, Math.round((points / weight) * 100)));
}

/** CSS var for the band's solid color — for bars/heroes that aren't `.score`. */
export function scoreBandVar(score: number | null | undefined): string {
	switch (scoreBand(score)) {
		case 'strong':
			return 'var(--strong)';
		case 'good':
			return 'var(--good)';
		case 'weak':
			return 'var(--weak)';
		default:
			return 'var(--faint)';
	}
}
