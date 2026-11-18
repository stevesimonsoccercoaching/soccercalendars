from __future__ import annotations

class ScheduleSafetyError(RuntimeError):
    """Raised when a schedule transition looks too suspicious to trust automatically."""

def _as_bool(value, *, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"Expected boolean safety setting, got {value!r}")

def _as_fraction(value, *, default):
    if value is None:
        return default
    try:
        fraction = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"max_missing_fraction must be a number between 0 and 1, got {value!r}"
        ) from exc
    if not 0 <= fraction <= 1:
        raise ValueError(
            f"max_missing_fraction must be between 0 and 1, got {fraction}"
        )
    return fraction

def validate_schedule_transition(
    old_matches,
    new_matches,
    safety_config=None,
    *,
    label="schedule",
):
    """
    Competition-neutral input validation.

    This function does not decide what a missing match means. It only decides
    whether the change between two exports looks safe enough for automation.
    """
    config = safety_config or {}

    reject_empty_export = _as_bool(
        config.get("reject_empty_export"),
        default=True,
    )
    max_missing_fraction = _as_fraction(
        config.get("max_missing_fraction"),
        default=0.5,
    )

    old_ids = set(old_matches)
    new_ids = set(new_matches)
    missing_ids = old_ids - new_ids
    added_ids = new_ids - old_ids

    old_count = len(old_ids)
    new_count = len(new_ids)
    missing_count = len(missing_ids)
    missing_fraction = missing_count / old_count if old_count else 0.0

    if old_count and reject_empty_export and new_count == 0:
        raise ScheduleSafetyError(
            f"{label}: suspicious export transition: previous export had "
            f"{old_count} match(es), but the new export has 0. "
            "Automatic missing-match inference was stopped."
        )

    if old_count and missing_fraction > max_missing_fraction:
        raise ScheduleSafetyError(
            f"{label}: suspicious export transition: "
            f"{missing_count} of {old_count} previous match(es) disappeared "
            f"({missing_fraction:.1%}), exceeding the configured "
            f"{max_missing_fraction:.1%} limit. "
            "Automatic missing-match inference was stopped."
        )

    return {
        "old_count": old_count,
        "new_count": new_count,
        "missing_count": missing_count,
        "added_count": len(added_ids),
        "missing_fraction": missing_fraction,
        "missing_ids": missing_ids,
        "added_ids": added_ids,
    }
