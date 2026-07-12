import { describe, expect, it } from 'vitest';
import { deriveProfile } from './profile';
import type { Resume } from '$lib/api';

function resume(extracted_text: string): Resume {
	return {
		id: 1,
		original_filename: 'resume.pdf',
		page_count: 1,
		is_active: true,
		uploaded_at: '2026-07-10T00:00:00Z',
		extracted_text
	};
}

describe('deriveProfile', () => {
	it('returns null when there is no resume', () => {
		expect(deriveProfile(null)).toBeNull();
		expect(deriveProfile(undefined)).toBeNull();
	});

	it('takes the first meaningful line as the name and derives two-letter initials', () => {
		const p = deriveProfile(resume('Herb Hagely\nSenior Software Engineer\n'));
		expect(p?.name).toBe('Herb Hagely');
		expect(p?.initials).toBe('HH');
	});

	it('reads a job title from the line below the name', () => {
		const p = deriveProfile(resume('Herb Hagely\nSenior Software Engineer\nSt. Louis, MO'));
		expect(p?.subtitle).toBe('Senior Software Engineer');
	});

	it('skips contact-info lines when hunting for a headline', () => {
		const p = deriveProfile(
			resume('Jane Doe\nherb@example.com | (555) 123-4567\nStaff Engineer\n')
		);
		expect(p?.subtitle).toBe('Staff Engineer');
	});

	it('skips URLs and addresses', () => {
		const p = deriveProfile(
			resume('Jane Doe\nlinkedin.com/in/janedoe\n123 Main St, St. Louis, MO 63101\nData Scientist')
		);
		expect(p?.subtitle).toBe('Data Scientist');
	});

	it('leaves the subtitle null when no plausible headline is found', () => {
		const p = deriveProfile(resume('Jane Doe\njane@example.com\nSUMMARY\nExperienced engineer...'));
		expect(p?.name).toBe('Jane Doe');
		expect(p?.subtitle).toBeNull();
	});

	it('ignores a leading RESUME / Curriculum Vitae banner', () => {
		const p = deriveProfile(resume('RESUME\nHerb Hagely\nSenior Software Engineer'));
		expect(p?.name).toBe('Herb Hagely');
		expect(p?.initials).toBe('HH');
	});

	it('handles a single-word name', () => {
		const p = deriveProfile(resume('Madonna\nPerformer'));
		expect(p?.name).toBe('Madonna');
		expect(p?.initials).toBe('M');
	});

	it('collapses stray whitespace in the name', () => {
		const p = deriveProfile(resume('  Herb   Hagely  \nEngineer'));
		expect(p?.name).toBe('Herb Hagely');
	});

	it('returns null when the resume text is empty or only a banner', () => {
		expect(deriveProfile(resume(''))).toBeNull();
		expect(deriveProfile(resume('  \n\n  '))).toBeNull();
		expect(deriveProfile(resume('Curriculum Vitae'))).toBeNull();
	});
});
