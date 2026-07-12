"""Dry-run analysis of source output vs. the hard filter.

Walks every configured source, runs ``evaluate`` on each fetched ``RawJob``,
and reports counts by ``(source, status, reason)`` plus a few sample
titles per bucket. Nothing is persisted.

Use when the funnel feels narrow: distinguishes "few jobs are being fetched"
from "lots of jobs are being fetched but the filter drops them all".
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlmodel import Session

from job_applier.filters import FilterConfig, evaluate, load_active_config
from job_applier.models import engine
from job_applier.sources import SourceAdapter, get_all_sources

log = logging.getLogger(__name__)

SAMPLE_PER_BUCKET = 5


@dataclass
class FilterDiagnostic:
    fetched_by_source: Counter = field(default_factory=Counter)
    # Bucket key format: "{status}:{reason}" so the same reason from different
    # statuses doesn't collide (manual-review and dropped use disjoint reasons
    # today, but the encoding stays robust if that changes).
    by_source: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    samples: dict[str, list[tuple[str, str, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def as_dict(self) -> dict:
        return {
            "fetched_by_source": dict(self.fetched_by_source),
            "by_source": {src: dict(counts) for src, counts in self.by_source.items()},
            "samples": {k: list(v) for k, v in self.samples.items()},
        }


def diagnose_filter(
    sources: list[SourceAdapter] | None = None,
    filter_config: FilterConfig | None = None,
) -> FilterDiagnostic:
    """Fetch from each source and bucket the filter outcome of every RawJob."""
    if filter_config is None:
        with Session(engine()) as session:
            filter_config = load_active_config(session)
    if sources is None:
        sources = get_all_sources(filter_config=filter_config)

    result = FilterDiagnostic()
    for source in sources:
        # Isolate each source: one failing adapter shouldn't abort the whole
        # report — the diagnostic is exactly the tool you'd reach for to find
        # out *which* source is failing.
        try:
            for raw in source.fetch():
                result.fetched_by_source[source.name] += 1
                decision = evaluate(raw, filter_config)
                bucket = f"{decision.status.value}:{decision.reason or 'ok'}"
                result.by_source[source.name][bucket] += 1
                if len(result.samples[bucket]) < SAMPLE_PER_BUCKET:
                    result.samples[bucket].append(
                        (source.name, raw.company_name, raw.title)
                    )
        except Exception as exc:  # noqa: BLE001 - report-and-continue, never abort
            log.warning("diagnose[%s] fetch failed: %s", source.name, exc)
            continue
    return result


def format_diagnostic(diag: FilterDiagnostic) -> str:
    """Human-readable text report. JSON output is available via ``as_dict``."""
    lines: list[str] = []
    lines.append("Filter diagnostic (no rows persisted)")
    lines.append("=" * 38)
    total_fetched = sum(diag.fetched_by_source.values())
    lines.append(f"Total fetched: {total_fetched}")
    lines.append("")
    lines.append("Per source:")
    for src in sorted(diag.fetched_by_source):
        fetched = diag.fetched_by_source[src]
        passed = sum(
            v for k, v in diag.by_source[src].items() if k.startswith("passed:")
        )
        manual = sum(
            v for k, v in diag.by_source[src].items() if k.startswith("manual:")
        )
        dropped = sum(
            v for k, v in diag.by_source[src].items() if k.startswith("dropped:")
        )
        lines.append(
            f"  {src:<20} fetched={fetched:>5}  passed={passed:>5}  "
            f"manual={manual:>4}  dropped={dropped:>5}"
        )
    lines.append("")
    lines.append("Drop reasons (across all sources):")
    drop_totals: Counter = Counter()
    for counts in diag.by_source.values():
        for bucket, n in counts.items():
            if bucket.startswith("dropped:"):
                drop_totals[bucket[len("dropped:") :]] += n
    for reason, n in drop_totals.most_common():
        lines.append(f"  {n:>5}  {reason}")
        sample_key = f"dropped:{reason}"
        for src, company, title in diag.samples.get(sample_key, [])[:3]:
            lines.append(f"          e.g. [{src}] {company} — {title}")
    return "\n".join(lines)
