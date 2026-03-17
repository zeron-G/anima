"""Field-level threshold diff engine — computes significance of state changes."""

from __future__ import annotations

from anima.models.perception_frame import DiffRule, FieldDiff, StateDiff
from anima.utils.logging import get_logger

log = get_logger("diff_engine")

# Alert thresholds (hard-coded, separate from diff significance)
_ALERT_THRESHOLDS = {
    "cpu_percent": 90.0,
    "memory_percent": 90.0,
    "disk_percent": 95.0,
}


class DiffEngine:
    """Computes field-level diffs between environment snapshots.

    Only fields that exceed their threshold are marked significant.
    Significance score is the fraction of rules that triggered.
    """

    def __init__(self, rules: list[DiffRule]) -> None:
        self._rules = {r.field: r for r in rules}

    def compute_diff(
        self, current: dict, previous: dict | None
    ) -> StateDiff:
        """Compare current snapshot to previous. Returns StateDiff."""
        if previous is None:
            # First snapshot — everything is "new" but not significant
            return StateDiff(significance_score=0.0, has_alerts=False)

        field_diffs: dict[str, FieldDiff] = {}
        significant_count = 0
        has_alerts = False

        for field_name, rule in self._rules.items():
            cur_val = current.get(field_name)
            prev_val = previous.get(field_name)

            if cur_val is None or prev_val is None:
                continue

            # Special case: file_changes is a list — threshold 0 means any change
            if field_name == "file_changes":
                count = len(cur_val) if isinstance(cur_val, list) else cur_val
                delta = float(count)
                is_significant = delta > rule.threshold
            else:
                try:
                    delta = abs(float(cur_val) - float(prev_val))
                except (TypeError, ValueError):
                    continue
                is_significant = delta > rule.threshold

            fd = FieldDiff(
                field=field_name,
                old_value=prev_val,
                new_value=cur_val,
                delta=delta,
                significant=is_significant,
            )
            field_diffs[field_name] = fd

            if is_significant:
                significant_count += 1

            # Check alert thresholds
            if field_name in _ALERT_THRESHOLDS:
                try:
                    if float(cur_val) >= _ALERT_THRESHOLDS[field_name]:
                        has_alerts = True
                except (TypeError, ValueError) as e:
                    log.debug("diff_engine: %s", e)

        # Significance score: fraction of rules that triggered
        total = len(self._rules)
        score = significant_count / total if total > 0 else 0.0

        return StateDiff(
            field_diffs=field_diffs,
            significance_score=score,
            has_alerts=has_alerts,
        )

    @classmethod
    def from_config(cls, diff_rules_cfg: dict) -> DiffEngine:
        """Create DiffEngine from config dict.

        Expected format:
            {"cpu_percent": {"threshold": 15}, ...}
        """
        rules = []
        for field_name, spec in diff_rules_cfg.items():
            threshold = spec.get("threshold", 0) if isinstance(spec, dict) else spec
            rules.append(DiffRule(field=field_name, threshold=float(threshold)))
        return cls(rules)
