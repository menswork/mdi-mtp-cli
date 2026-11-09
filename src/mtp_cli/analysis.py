"""Offline analysis of saved MTP reporting snapshots."""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path


INACTIVITY_BANDS = (
    ("Current", 0, 7),
    ("Attention", 8, 21),
    ("Stalled", 22, 89),
    ("Extended", 90, None),
)

INACTIVITY_LABELS = (
    "Current", "Attention", "Stalled", "Extended", "Unknown", "Future"
)


# Private confirmations are local data, never embedded in source code.
# These decisions affect review labels only; records are never merged.
DEFAULT_CONFIRMATIONS_PATH = Path("reports/duplicate-confirmations.json")


def load_duplicate_confirmations(path=None):
    """Load manually confirmed record-ID pairs from a private JSON file."""
    path = Path(path) if path is not None else DEFAULT_CONFIRMATIONS_PATH

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}

    if not isinstance(data, dict):
        raise ValueError("duplicate confirmations must be a JSON object")

    pairs = data.get("confirmed_pairs")
    if not isinstance(pairs, list):
        raise ValueError("confirmed_pairs must be a list")

    confirmations = {}
    for index, entry in enumerate(pairs):
        if not isinstance(entry, dict):
            raise ValueError(f"confirmation {index} must be an object")

        ids = entry.get("ids")
        reason = entry.get("reason")

        if (
            not isinstance(ids, list)
            or len(ids) != 2
            or not all(isinstance(value, str) and value.strip()
                       for value in ids)
            or ids[0] == ids[1]
        ):
            raise ValueError(f"confirmation {index} has invalid IDs")

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"confirmation {index} has invalid reason")

        key = frozenset(ids)
        if key in confirmations:
            raise ValueError(f"confirmation {index} duplicates another pair")

        confirmations[key] = reason

    return confirmations


def _parse_date(value):
    if not value:
        return None
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _normalize_status(value):
    """Normalize the tracker's no-data status without changing other labels."""
    status = value or "Other / No Data"
    if status.strip().casefold() in {"no data", "other / no data"}:
        return "Other / No Data"
    return status


def _inactivity_band(latest_date, reference_date):
    if latest_date is None:
        return "Unknown", None

    days = (reference_date - latest_date).days
    if days < 0:
        return "Future", days

    for name, lower, upper in INACTIVITY_BANDS:
        if days >= lower and (upper is None or days <= upper):
            return name, days



def _trainee_name(record):
    """Return a usable trainee name without coercing invalid values."""
    name = record.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    parts = []
    for field in ("firstName", "lastName"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    return " ".join(parts)


def _duplicate_review(trainees, confirmations=None):
    """Group repeated-name records for human review; never merge them.

    Sorting all name tokens can create false positives for multi-part names.
    A group is a review candidate, not an identity determination.
    """
    if confirmations is None:
        confirmations = {}

    grouped = defaultdict(list)

    def normalized_name(record):
        name = _trainee_name(record)
        # Ignore capitalization, whitespace, comma placement, and
        # first-name/last-name order. Sorting can collide for distinct
        # multi-part names, so this is only a review grouping.
        parts = name.replace(",", " ").casefold().split()
        return " ".join(sorted(parts))

    def account_path(record):
        trainee_id = str(record.get("id") or "")
        if "/posts/" in trainee_id:
            return trainee_id.split("/posts/", 1)[0]
        return None

    def completed_milestones(record):
        return {
            str(item.get("milestone") or "").strip().casefold()
            for item in (record.get("milestones") or [])
            if item.get("done") and item.get("milestone")
        }

    def record_summary(record, fallback_name):
        return {
            "id": record.get("id"),
            "name": _trainee_name(record) or fallback_name,
            "division": record.get("division"),
            "team": record.get("team"),
            "status": record.get("status"),
            "mentor": record.get("mentor"),
            "latest_date": record.get("latestDate"),
            "completed_milestones": len(
                completed_milestones(record)
            ),
        }

    for record in trainees:
        key = normalized_name(record)
        if key:
            grouped[key].append(record)

    groups = []
    for key, records in sorted(grouped.items()):
        if len(records) < 2:
            continue

        comparisons = []
        pair_confidences = []

        for index, first in enumerate(records):
            for second in records[index + 1:]:
                evidence = []
                first_milestones = completed_milestones(first)
                second_milestones = completed_milestones(second)
                shared = sorted(first_milestones & second_milestones)
                first_only = sorted(first_milestones - second_milestones)
                second_only = sorted(second_milestones - first_milestones)

                same_division = (
                    bool(first.get("division"))
                    and first.get("division") == second.get("division")
                )
                same_team = (
                    bool(first.get("team"))
                    and first.get("team") == second.get("team")
                )
                same_mentor = (
                    bool(first.get("mentor"))
                    and first.get("mentor") == second.get("mentor")
                )
                first_account = account_path(first)
                same_account = (
                    bool(first_account)
                    and first_account == account_path(second)
                )

                if same_division:
                    evidence.append("Same division")
                if same_team:
                    evidence.append("Same team")
                if same_mentor:
                    evidence.append("Same mentor")
                if same_account:
                    evidence.append("Same tracker account path")
                evidence.append(
                    f"{len(shared)} shared completed milestone(s)"
                )
                if shared:
                    evidence.append(
                        "Shared milestones: " + ", ".join(shared)
                    )
                if first_only or second_only:
                    evidence.append(
                        f"Milestones unique to first/second record: "
                        f"{len(first_only)}/{len(second_only)}"
                    )

                # Exact manually confirmed pairs take precedence over heuristics.
                first_id = first.get("id")
                second_id = second.get("id")

                manual_confirmation = None
                if first_id and second_id:
                    pair_ids = frozenset({
                        str(first_id),
                        str(second_id),
                    })
                    manual_confirmation = confirmations.get(pair_ids)

                if manual_confirmation:
                    confidence = "Confirmed"
                    evidence.append(manual_confirmation)
                elif (
                    same_division and same_team and len(shared) >= 3
                ) or (
                    same_account and len(shared) >= 3
                ):
                    confidence = "High"
                elif (
                    (same_division and same_team)
                    or same_account
                    or len(shared) >= 1
                ):
                    confidence = "Review"
                else:
                    confidence = "Low"

                pair_confidences.append(confidence)

                comparisons.append({
                    "first": record_summary(first, key),
                    "second": record_summary(second, key),
                    "confidence": confidence,
                    "evidence": evidence,
                })

        rank = {
            "Low": 0, "Review": 1, "High": 2, "Confirmed": 3
        }
        # Group confidence is the strongest pairwise assessment,
        # not a determination that every record is the same person.
        group_confidence = max(
            pair_confidences, key=lambda value: rank[value]
        )
        lowest_pair_confidence = min(
            pair_confidences, key=lambda value: rank[value]
        )

        groups.append({
            "name": _trainee_name(records[0]) or key,
            "record_count": len(records),
            "confidence": group_confidence,
            "lowest_pair_confidence": lowest_pair_confidence,
            "records": [
                record_summary(record, key)
                for record in records
            ],
            "comparisons": comparisons,
        })

    return {
        "repeated_name_groups": len(groups),
        "records_in_groups": sum(
            group["record_count"] for group in groups
        ),
        "groups": groups,
    }


def _validate_snapshot(snapshot: dict) -> dt.date:
    """Validate a snapshot and return its reference date."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a JSON object")

    if snapshot.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema version")

    timestamp = snapshot.get("collected_at")
    if not isinstance(timestamp, str):
        raise ValueError(
            "snapshot has an invalid collected_at timestamp"
        )

    try:
        collected_at = dt.datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        )
        if (
            collected_at.tzinfo is None
            or collected_at.utcoffset() is None
        ):
            raise ValueError
        reference_date = collected_at.date()
    except (TypeError, ValueError):
        raise ValueError(
            "snapshot has an invalid collected_at timestamp"
        ) from None

    trainees = snapshot.get("trainees")
    if not isinstance(trainees, list):
        raise ValueError("snapshot trainees must be a list")

    for index, trainee in enumerate(trainees):
        if not isinstance(trainee, dict):
            raise ValueError(
                f"snapshot trainee at index {index} must be an object"
            )

        milestones = trainee.get("milestones")
        if milestones is None:
            continue
        if not isinstance(milestones, list):
            raise ValueError(
                f"snapshot trainee at index {index} milestones must be a list"
            )
        for milestone_index, milestone in enumerate(milestones):
            if not isinstance(milestone, dict):
                raise ValueError(
                    f"snapshot trainee at index {index} milestone "
                    f"{milestone_index} must be an object"
                )

    return reference_date


def analyze_snapshot(snapshot: dict) -> dict:
    """Analyze a saved snapshot without network access or tracker changes."""
    reference_date = _validate_snapshot(snapshot)
    trainees = snapshot["trainees"]

    status_counts = Counter()
    division_counts = defaultdict(Counter)
    team_counts = defaultdict(Counter)
    division_progress = defaultdict(lambda: [0, 0])
    team_progress = defaultdict(lambda: [0, 0])

    mentor_active = Counter()
    mentor_attention = defaultdict(Counter)
    inactivity_counts = Counter()
    inactivity_trainees = {name: [] for name in INACTIVITY_LABELS}

    missing_mentor = 0
    missing_latest_date = 0
    future_latest_date = 0
    malformed_latest_date = 0
    data_entry_errors = 0
    duplicate_ids = 0
    seen_ids = set()

    for trainee in trainees:
        status = _normalize_status(trainee.get("status"))
        division = trainee.get("division") or "Unassigned"
        team = trainee.get("team") or "Unassigned"
        mentor = trainee.get("mentor") or "Unassigned"

        status_counts[status] += 1
        division_counts[division][status] += 1
        team_counts[team][status] += 1

        trainee_id = trainee.get("id")
        if trainee_id is not None:
            id_key = str(trainee_id)
            if id_key in seen_ids:
                duplicate_ids += 1
            else:
                seen_ids.add(id_key)

        if not trainee.get("mentor"):
            missing_mentor += 1

        latest_raw = trainee.get("latestDate")
        latest_date = _parse_date(latest_raw)

        if not latest_raw:
            missing_latest_date += 1
        elif latest_date is None:
            malformed_latest_date += 1
        elif latest_date > reference_date:
            future_latest_date += 1

        milestones = trainee.get("milestones") or []
        completed = sum(bool(m.get("done")) for m in milestones)
        total = len(milestones)

        division_progress[division][0] += completed
        division_progress[division][1] += total
        team_progress[team][0] += completed
        team_progress[team][1] += total

        data_entry_errors += sum(
            bool(m.get("dataEntryError")) for m in milestones
        )

        if status != "In progress":
            continue

        band, days = _inactivity_band(latest_date, reference_date)
        inactivity_counts[band] += 1
        mentor_active[mentor] += 1
        mentor_attention[mentor][band] += 1

        name = _trainee_name(trainee) or "Unnamed trainee"

        inactivity_trainees[band].append({
            "id": trainee_id,
            "name": name,
            "division": division,
            "team": team,
            "mentor": mentor,
            "latest_date": latest_raw,
            "days_since_activity": days,
        })

    def group_summary(groups, progress):
        result = {}
        for name, counts in sorted(groups.items()):
            done, total = progress[name]
            result[name] = {
                "trainees": sum(counts.values()),
                "in_progress": counts.get("In progress", 0),
                "completed_program": counts.get("Completed program", 0),
                "milestones_done": done,
                "milestones_total": total,
                "milestone_completion_percent": (
                    round(100 * done / total, 1) if total else None
                ),
                "statuses": dict(sorted(counts.items())),
            }
        return result

    for band in INACTIVITY_LABELS:
        inactivity_trainees[band].sort(
            key=lambda item: (
                item["days_since_activity"] is None,
                -(item["days_since_activity"] or 0),
                item["name"].casefold(),
            )
        )

    mentor_attention_summary = {}
    for mentor, counts in sorted(
        mentor_attention.items(),
        key=lambda item: (-sum(item[1].values()), item[0].casefold()),
    ):
        mentor_attention_summary[mentor] = {
            "total_in_progress": sum(counts.values()),
            "inactivity": {
                band: counts.get(band, 0) for band in INACTIVITY_LABELS
            },
        }

    return {
        "reference_date": reference_date.isoformat(),
        "total_trainees": len(trainees),
        "status_counts": dict(sorted(status_counts.items())),
        "inactivity_counts": {
            band: inactivity_counts.get(band, 0)
            for band in INACTIVITY_LABELS
        },
        "inactivity_trainees": inactivity_trainees,
        "division_summary": group_summary(
            division_counts, division_progress
        ),
        "team_summary": group_summary(team_counts, team_progress),
        "active_mentor_workload": dict(
            sorted(
                mentor_active.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ),
        "mentor_attention_summary": mentor_attention_summary,
        "duplicate_review": _duplicate_review(
            trainees, load_duplicate_confirmations()
        ),
        "data_quality": {
            "missing_mentor": missing_mentor,
            "missing_latest_date": missing_latest_date,
            "future_latest_date": future_latest_date,
            "malformed_latest_date": malformed_latest_date,
            "milestone_data_entry_errors": data_entry_errors,
            "duplicate_trainee_ids": duplicate_ids,
        },
    }
