from __future__ import annotations

from harlequin.autocomplete.completion import HarlequinCompletion


_CLICKHOUSE_COMPLETIONS: tuple[tuple[str, str, int], ...] = (
    ("numbers", "fn", 900),
    ("numbers_mt", "fn", 900),
    ("file", "fn", 900),
    ("s3", "fn", 900),
    ("url", "fn", 900),
    ("remote", "fn", 850),
    ("cluster", "fn", 850),
    ("mergeTreeIndex", "fn", 800),
    ("toDateTime64", "fn", 800),
    ("toStartOfInterval", "fn", 800),
    ("quantile", "fn", 800),
    ("quantiles", "fn", 800),
    ("argMax", "fn", 800),
    ("argMin", "fn", 800),
    ("arrayJoin", "fn", 800),
    ("PREWHERE", "kw", 800),
    ("FINAL", "kw", 800),
    ("SETTINGS", "kw", 800),
    ("FORMAT", "kw", 800),
    ("ENGINE", "kw", 800),
    ("MergeTree", "kw", 800),
)


def get_completion_data() -> list[HarlequinCompletion]:
    return [
        HarlequinCompletion(
            label=label,
            type_label=type_label,
            value=label,
            priority=priority,
            context=None,
        )
        for label, type_label, priority in _CLICKHOUSE_COMPLETIONS
    ]
