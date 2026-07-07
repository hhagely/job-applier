import { describe, expect, it } from 'vitest';
import { scoreBand, scoreBandVar } from './score';

describe('scoreBand', () => {
	it('maps to the brand-spec thresholds (80 / 65)', () => {
		expect(scoreBand(80)).toBe('strong');
		expect(scoreBand(100)).toBe('strong');
		expect(scoreBand(79)).toBe('good');
		expect(scoreBand(65)).toBe('good');
		expect(scoreBand(64)).toBe('weak');
		expect(scoreBand(0)).toBe('weak');
	});

	it('treats null/undefined as none', () => {
		expect(scoreBand(null)).toBe('none');
		expect(scoreBand(undefined)).toBe('none');
	});

	it('exposes a matching CSS var per band', () => {
		expect(scoreBandVar(85)).toBe('var(--strong)');
		expect(scoreBandVar(70)).toBe('var(--good)');
		expect(scoreBandVar(50)).toBe('var(--weak)');
		expect(scoreBandVar(null)).toBe('var(--faint)');
	});
});
