"""Compare two saved MTP reporting snapshots without network access."""

from __future__ import annotations

from collections import Counter

from .analysis import (
    _inactivity_band,
    _normalize_status,
    _parse_date,
    _trainee_name,
    _validate_snapshot,
)


def _record_id(record):
    value = record.get("id")
    return str(value) if value is not None and str(value).strip() else None


def _index_records(trainees):
    """Index unique IDs and separately identify ambiguous or missing IDs."""
    grouped = {}
    missing_id_count = 0

    for record in trainees:
        record_id = _record_id(record)
        if record_id is None:
            missing_id_count += 1
            continue
        grouped.setdefault(record_id, []).append(record)

    duplicates = sorted(
        record_id for record_id, records in grouped.items()
        if len(records) > 1
    )

    unique = {
        record_id: records[0]
        for record_id, records in grouped.items()
        if len(records) == 1
    }

    return unique, duplicates, missing_id_count


def _completed_milestones(record):
    """Return completed milestones keyed by goal ID, with a name fallback."""
    completed = {}

    for item in record.get("milestones") or []:
        if not item.get("done"):
            continue

        name = str(item.get("milestone") or "").strip()
        goal_id = item.get("goalId")

        if goal_id is not None and str(goal_id).strip():
            identity = ("goal", str(goal_id).strip())
        elif name:
            identity = ("name", name.casefold())
        else:
            continue

        completed[identity] = (name or str(goal_id)).casefold()

    return completed


def _milestone_totals(trainees):
    total = 0
    completed = 0

    for record in trainees:
        milestones = record.get("milestones") or []
        total += len(milestones)
        completed += sum(bool(item.get("done")) for item in milestones)

    return {"completed": completed, "total": total}


def _inactivity(record, reference_date):
    if _normalize_status(record.get("status")) != "In progress":
        return None

    latest_date = _parse_date(record.get("latestDate"))
    band, days = _inactivity_band(latest_date, reference_date)

    return {
        "band": band,
        "days_since_activity": days,
        "latest_date": record.get("latestDate"),
    }


def _record_summary(record, reference_date):
    return {
        "id": record.get("id"),
        "name": _trainee_name(record) or "Unnamed trainee",
        "status": _normalize_status(record.get("status")),
        "division": record.get("division") or "Unassigned",
        "team": record.get("team") or "Unassigned",
        "mentor": record.get("mentor") or "Unassigned",
        "inactivity": _inactivity(record, reference_date),
        "completed_milestones": len(_completed_milestones(record)),
    }


def compare_snapshots(older: dict, newer: dict) -> dict:
    """Compare two valid snapshots, matching only unique, nonempty IDs."""
    old_reference_date = _validate_snapshot(older)
    new_reference_date = _validate_snapshot(newer)

    old_date = old_reference_date.isoformat()
    new_date = new_reference_date.isoformat()

    old_status_counts = Counter(
        _normalize_status(record.get("status"))
        for record in older["trainees"]
    )
    new_status_counts = Counter(
        _normalize_status(record.get("status"))
        for record in newer["trainees"]
    )

    old_records, old_duplicates, old_missing_ids = _index_records(
        older["trainees"]
    )
    new_records, new_duplicates, new_missing_ids = _index_records(
        newer["trainees"]
    )

    old_ids = set(old_records)
    new_ids = set(new_records)

    added = [
        _record_summary(new_records[key], new_reference_date)
        for key in sorted(new_ids - old_ids)
    ]
    missing = [
        _record_summary(old_records[key], old_reference_date)
        for key in sorted(old_ids - new_ids)
    ]

    changed = []
    inactivity_changed = []

    for record_id in sorted(old_ids & new_ids):
        old = old_records[record_id]
        new = new_records[record_id]
        fields = {}

        comparisons = (
            ("status", _normalize_status(old.get("status")),
             _normalize_status(new.get("status"))),
            ("division", old.get("division") or "Unassigned",
             new.get("division") or "Unassigned"),
            ("team", old.get("team") or "Unassigned",
             new.get("team") or "Unassigned"),
            ("mentor", old.get("mentor") or "Unassigned",
             new.get("mentor") or "Unassigned"),
            ("latest_date", old.get("latestDate"), new.get("latestDate")),
        )

        for field, old_value, new_value in comparisons:
            if old_value != new_value:
                fields[field] = {
                    "before": old_value,
                    "after": new_value,
                }

        old_completed = _completed_milestones(old)
        new_completed = _completed_milestones(new)

        newly_completed = sorted(
            new_completed[key]
            for key in new_completed.keys() - old_completed.keys()
        )
        no_longer_completed = sorted(
            old_completed[key]
            for key in old_completed.keys() - new_completed.keys()
        )

        if newly_completed or no_longer_completed:
            fields["milestones"] = {
                "newly_completed": newly_completed,
                "no_longer_completed": no_longer_completed,
            }

        old_inactivity = _inactivity(old, old_reference_date)
        new_inactivity = _inactivity(new, new_reference_date)

        old_band = (
            old_inactivity["band"] if old_inactivity is not None else None
        )
        new_band = (
            new_inactivity["band"] if new_inactivity is not None else None
        )

        # Compare category or in-progress membership, not elapsed days.
        if old_band != new_band:
            inactivity_changed.append({
                "id": new.get("id"),
                "name": _trainee_name(new) or _trainee_name(old) or "Unnamed trainee",
                "before": old_inactivity,
                "after": new_inactivity,
            })

        if fields:
            changed.append({
                "id": new.get("id"),
                "name": _trainee_name(new) or _trainee_name(old) or "Unnamed trainee",
                "changes": fields,
            })

    status_names = sorted(
        set(old_status_counts)
        | set(new_status_counts)
    )
    status_changes = {
        status: {
            "before": old_status_counts.get(status, 0),
            "after": new_status_counts.get(status, 0),
            "change": (
                new_status_counts.get(status, 0)
                - old_status_counts.get(status, 0)
            ),
        }
        for status in status_names
    }

    old_milestones = _milestone_totals(older["trainees"])
    new_milestones = _milestone_totals(newer["trainees"])

    return {
        "older_snapshot_date": old_date,
        "newer_snapshot_date": new_date,
        "elapsed_days": (new_reference_date - old_reference_date).days,
        "totals": {
            "before": len(older["trainees"]),
            "after": len(newer["trainees"]),
            "change": (
                len(newer["trainees"])
                - len(older["trainees"])
            ),
        },
        "status_changes": status_changes,
        "records": {
            "added": added,
            "missing_from_newer": missing,
            "changed": changed,
            "inactivity_changed": inactivity_changed,
            "added_count": len(added),
            "missing_from_newer_count": len(missing),
            "changed_count": len(changed),
            "inactivity_changed_count": len(inactivity_changed),
        },
        "milestone_progress": {
            "before": old_milestones,
            "after": new_milestones,
            "completed_change": (
                new_milestones["completed"] - old_milestones["completed"]
            ),
            "total_change": new_milestones["total"] - old_milestones["total"],
        },
        "ambiguous_ids": {
            "older": old_duplicates,
            "newer": new_duplicates,
        },
        "records_without_ids": {
            "older": old_missing_ids,
            "newer": new_missing_ids,
        },
    }
