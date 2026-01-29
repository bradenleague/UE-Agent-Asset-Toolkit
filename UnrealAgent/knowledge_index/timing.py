"""
Timing instrumentation for indexing performance analysis.

Usage:
    from knowledge_index.timing import IndexTimer

    timer = IndexTimer()

    with timer.phase("discovery"):
        # scan files

    with timer.phase("batch_fast"):
        # classify assets

    timer.report()  # Print summary
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from contextlib import contextmanager


@dataclass
class PhaseStats:
    """Stats for a single phase."""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    items_processed: int = 0
    bytes_processed: int = 0

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def items_per_second(self) -> float:
        if self.duration > 0:
            return self.items_processed / self.duration
        return 0.0


@dataclass
class IndexTimer:
    """
    Timer for tracking indexing performance across phases.

    Phases:
    - discovery: File system scanning (rglob)
    - batch_fast: Phase 1 classification (header-only)
    - skip_refs_store: Phase 2a direct storage (no refs needed)
    - batch_refs: Phase 2b reference extraction + storage
    - semantic_parse: Phase 3 parsing (batch-blueprint, batch-widget, etc.)
    - semantic_store: Phase 3 database writes
    - embedding: Optional embedding generation
    """

    phases: dict = field(default_factory=dict)
    total_start: float = 0.0
    total_end: float = 0.0

    # Counters for detailed metrics
    total_assets: int = 0
    lightweight_assets: int = 0
    semantic_assets: int = 0
    db_writes: int = 0
    subprocess_calls: int = 0
    embeddings_generated: int = 0

    def start(self):
        """Start the overall timer."""
        self.total_start = time.perf_counter()

    def stop(self):
        """Stop the overall timer."""
        self.total_end = time.perf_counter()

    @contextmanager
    def phase(self, name: str, items: int = 0):
        """Context manager for timing a phase."""
        if name not in self.phases:
            self.phases[name] = PhaseStats(name=name)

        stats = self.phases[name]
        phase_start = time.perf_counter()

        try:
            yield stats
        finally:
            phase_end = time.perf_counter()
            # Accumulate time (phases can be entered multiple times)
            if stats.start_time == 0:
                stats.start_time = phase_start
            stats.end_time = phase_end
            stats.items_processed += items

    def add_items(self, phase_name: str, count: int):
        """Add items to a phase counter."""
        if phase_name in self.phases:
            self.phases[phase_name].items_processed += count

    def increment_counter(self, counter_name: str, amount: int = 1):
        """Increment a counter (db_writes, subprocess_calls, etc.)."""
        if hasattr(self, counter_name):
            setattr(self, counter_name, getattr(self, counter_name) + amount)

    @property
    def total_duration(self) -> float:
        if self.total_end > 0:
            return self.total_end - self.total_start
        return time.perf_counter() - self.total_start

    def report(self) -> str:
        """Generate a timing report."""
        lines = []
        lines.append("=" * 60)
        lines.append("INDEXING PERFORMANCE REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Overall stats
        lines.append(f"Total time: {self._format_duration(self.total_duration)}")
        lines.append(f"Total assets: {self.total_assets:,}")
        if self.total_assets > 0 and self.total_duration > 0:
            lines.append(f"Overall rate: {self.total_assets / self.total_duration:.1f} assets/sec")
        lines.append("")

        # Phase breakdown
        lines.append("PHASE BREAKDOWN")
        lines.append("-" * 60)

        # Calculate phase durations properly (they may overlap due to subprocess overhead)
        phase_order = [
            "discovery",
            "batch_fast",
            "skip_refs_store",
            "batch_refs",
            "semantic_parse",
            "semantic_store",
            "embedding",
        ]

        for phase_name in phase_order:
            if phase_name in self.phases:
                stats = self.phases[phase_name]
                pct = (stats.duration / self.total_duration * 100) if self.total_duration > 0 else 0
                rate_str = f" ({stats.items_per_second:.1f}/sec)" if stats.items_processed > 0 else ""

                lines.append(
                    f"  {phase_name:20s}: {self._format_duration(stats.duration):>10s} "
                    f"({pct:5.1f}%) - {stats.items_processed:,} items{rate_str}"
                )

        # Add any phases not in the predefined order
        for phase_name, stats in self.phases.items():
            if phase_name not in phase_order:
                pct = (stats.duration / self.total_duration * 100) if self.total_duration > 0 else 0
                lines.append(
                    f"  {phase_name:20s}: {self._format_duration(stats.duration):>10s} "
                    f"({pct:5.1f}%) - {stats.items_processed:,} items"
                )

        lines.append("")

        # Counters
        lines.append("DETAILED METRICS")
        lines.append("-" * 60)
        lines.append(f"  Lightweight assets: {self.lightweight_assets:,}")
        lines.append(f"  Semantic assets:    {self.semantic_assets:,}")
        lines.append(f"  Database writes:    {self.db_writes:,}")
        lines.append(f"  Subprocess calls:   {self.subprocess_calls:,}")
        lines.append(f"  Embeddings:         {self.embeddings_generated:,}")

        # Performance indicators
        if self.subprocess_calls > 0 and self.total_assets > 0:
            assets_per_subprocess = self.total_assets / self.subprocess_calls
            lines.append(f"  Assets/subprocess:  {assets_per_subprocess:.1f}")

        if self.db_writes > 0:
            if "semantic_store" in self.phases:
                store_time = self.phases["semantic_store"].duration
                if store_time > 0:
                    lines.append(f"  DB writes/sec:      {self.db_writes / store_time:.1f}")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _format_duration(self, seconds: float) -> str:
        """Format duration as human-readable string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = seconds % 60
            return f"{mins}m {secs:.0f}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def to_dict(self) -> dict:
        """Export timing data as dict for logging/analysis."""
        return {
            "total_duration": self.total_duration,
            "total_assets": self.total_assets,
            "lightweight_assets": self.lightweight_assets,
            "semantic_assets": self.semantic_assets,
            "db_writes": self.db_writes,
            "subprocess_calls": self.subprocess_calls,
            "embeddings_generated": self.embeddings_generated,
            "phases": {
                name: {
                    "duration": stats.duration,
                    "items": stats.items_processed,
                    "rate": stats.items_per_second,
                }
                for name, stats in self.phases.items()
            }
        }


# Global timer instance for easy access
_global_timer: Optional[IndexTimer] = None


def get_timer() -> IndexTimer:
    """Get or create the global timer instance."""
    global _global_timer
    if _global_timer is None:
        _global_timer = IndexTimer()
    return _global_timer


def reset_timer():
    """Reset the global timer."""
    global _global_timer
    _global_timer = IndexTimer()
    return _global_timer
