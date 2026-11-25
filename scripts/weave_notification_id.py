#!/usr/bin/env python3
"""Stable identifiers for retried Team App change notifications."""

import hashlib
import json


def meaningful_events(events, watched_fields):
    """Keep only schedule fields relevant to change detection."""
    result = {}

    for uid, event in sorted(events.items()):
        fields = {}

        for field, _label in watched_fields:
            value = str(event.get(field) or "").strip()

            if field == "match_status" and not value:
                value = "Scheduled"

            fields[field] = value

        result[uid] = fields

    return result


def notification_id(previous_snapshot, current_events, watched_fields):
    """Identify the meaningful transition from baseline to current."""
    previous_payload = json.loads(previous_snapshot)

    if not isinstance(previous_payload, dict):
        raise ValueError("Invalid previous watch snapshot")

    previous_events = previous_payload.get("events")

    if not isinstance(previous_events, dict):
        raise ValueError("Previous snapshot has no valid events")

    transition = {
        "before": meaningful_events(previous_events, watched_fields),
        "after": meaningful_events(current_events, watched_fields),
    }

    encoded = json.dumps(
        transition,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()[:24]
