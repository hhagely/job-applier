// Derives the sidebar user-chip identity from the active resume. pypdf gives us
// plain text with no real structure (see resume_io.py), but the top of a resume
// is a strong convention: the first non-empty line is the name, and a headline
// (job title) usually follows within the next few lines. Everything here is
// best-effort and fails soft to `null` / name-only so a weird resume never
// breaks the shell. Kept as a pure function so it can be unit-tested.
import type { Resume } from '$lib/api';

export interface ShellProfile {
	/** Display name — the first meaningful line of the resume. */
	name: string;
	/** Best-effort headline under the name, or null if none looked plausible. */
	subtitle: string | null;
	/** 1-2 letter avatar monogram derived from the name. */
	initials: string;
}

// Generous safety caps against pathological single-line PDFs; visual fit is
// handled by CSS ellipsis, so these only guard the DOM, not the layout.
const MAX_NAME = 80;
const MAX_SUBTITLE = 80;
// How far past the name to hunt for a headline before giving up.
const HEADLINE_WINDOW = 6;

// Leading lines that are document headings, not a name.
const HEADING = /^(resume|cv|curriculum\s+vitae)$/i;
// Section headers that are never a personal headline.
const SECTION =
	/^(summary|professional\s+summary|experience|work\s+experience|education|skills|technical\s+skills|contact|objective|profile|about)\b/i;
const URLISH = /\b(https?:\/\/|www\.|linkedin\.com|github\.com|@)/i;

const collapse = (s: string) => s.replace(/\s+/g, ' ').trim();

function meaningfulLines(text: string): string[] {
	return text
		.split(/\r?\n/)
		.map(collapse)
		.filter(Boolean);
}

function deriveInitials(name: string): string {
	const words = name.split(' ').filter((w) => /[a-z]/i.test(w));
	if (words.length === 0) return name.slice(0, 1).toUpperCase() || '?';
	if (words.length === 1) return words[0].slice(0, 1).toUpperCase();
	return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

// A line reads as a headline (e.g. "Senior Software Engineer") rather than
// contact info: email/phone/address/links. Section headers are handled by the
// caller as a hard stop, so they aren't considered here.
function looksLikeHeadline(line: string): boolean {
	if (line.length < 2 || line.length > 60) return false;
	if (URLISH.test(line)) return false; // email / links
	const digits = (line.match(/\d/g) ?? []).length;
	if (digits >= 5) return false; // phone / zip / street address
	return /[a-z]/i.test(line);
}

/**
 * Build the sidebar identity chip from the active resume, or `null` when there
 * is no resume (the caller renders a placeholder that invites an upload).
 */
export function deriveProfile(resume: Resume | null | undefined): ShellProfile | null {
	if (!resume) return null;

	const lines = meaningfulLines(resume.extracted_text ?? '');
	// Skip a leading "RESUME" / "Curriculum Vitae" banner if present.
	let i = 0;
	while (i < lines.length && HEADING.test(lines[i])) i++;
	if (i >= lines.length) return null; // nothing usable — treat as no profile

	const name = lines[i].slice(0, MAX_NAME);

	// Hunt the lines just below the name for a headline. A section header
	// ("Summary", "Experience", ...) ends the resume's header block, so once we
	// hit one we stop rather than grab the body prose that follows it.
	let subtitle: string | null = null;
	for (let j = i + 1; j < Math.min(lines.length, i + 1 + HEADLINE_WINDOW); j++) {
		if (SECTION.test(lines[j])) break;
		if (looksLikeHeadline(lines[j])) {
			subtitle = lines[j].slice(0, MAX_SUBTITLE);
			break;
		}
	}

	return { name, subtitle, initials: deriveInitials(name) };
}
