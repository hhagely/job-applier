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
