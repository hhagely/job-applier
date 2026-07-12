// Per-source display label + apply-friction ("ease") rating. Shared by the
// queue, job detail, and dashboard so a source only needs describing once.

export type Ease = 'easy' | 'med' | 'hard';

export interface SourceMeta {
	label: string;
	ease: Ease;
}

export const SOURCE_META: Record<string, SourceMeta> = {
	greenhouse: { label: 'Greenhouse', ease: 'easy' },
	lever: { label: 'Lever', ease: 'easy' },
	ashby: { label: 'Ashby', ease: 'easy' },
	workable: { label: 'Workable', ease: 'easy' },
	smartrecruiters: { label: 'SmartRecruiters', ease: 'easy' },
	jibe: { label: 'Jibe', ease: 'med' },
	oracle: { label: 'Oracle', ease: 'med' },
	remoteok: { label: 'RemoteOK', ease: 'med' },
	weworkremotely: { label: 'WWR', ease: 'med' },
	workday: { label: 'Workday', ease: 'hard' },
	hackernews: { label: 'HN', ease: 'med' },
	ycombinator: { label: 'YC', ease: 'med' }
};

export function sourceInfo(source: string): SourceMeta {
	return SOURCE_META[source] ?? { label: source, ease: 'med' };
}
