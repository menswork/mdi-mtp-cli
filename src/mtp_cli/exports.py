"""CSV exports generated from saved MTP snapshots."""

from __future__ import annotations

import csv
from pathlib import Path

from .analysis import INACTIVITY_LABELS, analyze_snapshot


EXPORT_FIELDS = {
    "inactivity": [
        "name",
        "id",
        "inactivity_band",
        "days_since_activity",
        "latest_date",
        "mentor",
        "division",
        "team",
    ],
    "mentors": [
        "mentor",
        "total_in_progress",
        *[f"inactivity_{band.lower()}" for band in INACTIVITY_LABELS],
    ],
    "duplicates": [
        "group_name",
        "group_confidence",
        "lowest_pair_confidence",
        "record_count",
        "record_name",
        "record_id",
        "division",
        "team",
        "status",
        "mentor",
        "latest_date",
        "completed_milestones",
        "comparison_evidence",
    ],
}


def export_rows(result: dict, export_type: str) -> list[dict]:
    """Return flat CSV rows for one supported export type."""
    if export_type not in EXPORT_FIELDS:
        raise ValueError(f"unknown export type: {export_type}")

    rows = []

    if export_type == "inactivity":
        for band in INACTIVITY_LABELS:
            for person in result["inactivity_trainees"][band]:
                rows.append({
                    "name": person["name"],
                    "id": person["id"],
                    "inactivity_band": band,
                    "days_since_activity": person["days_since_activity"],
                    "latest_date": person["latest_date"],
                    "mentor": person["mentor"],
                    "division": person["division"],
                    "team": person["team"],
                })

    elif export_type == "mentors":
        for mentor, item in result["mentor_attention_summary"].items():
            row = {
                "mentor": mentor,
                "total_in_progress": item["total_in_progress"],
            }
            for band in INACTIVITY_LABELS:
                row[f"inactivity_{band.lower()}"] = (
                    item["inactivity"][band]
                )
            rows.append(row)

    elif export_type == "duplicates":
        for group in result["duplicate_review"]["groups"]:
            evidence = []
            for comparison in group["comparisons"]:
                evidence.append(
                    f"{comparison['confidence']}: "
                    + "; ".join(comparison["evidence"])
                )

            evidence_text = " | ".join(evidence)

            for record in group["records"]:
                rows.append({
                    "group_name": group["name"],
                    "group_confidence": group["confidence"],
                    "lowest_pair_confidence": group[
                        "lowest_pair_confidence"
                    ],
                    "record_count": group["record_count"],
                    "record_name": record["name"],
                    "record_id": record["id"],
                    "division": record["division"],
                    "team": record["team"],
                    "status": record["status"],
                    "mentor": record["mentor"],
                    "latest_date": record["latest_date"],
                    "completed_milestones": record["completed_milestones"],
                    "comparison_evidence": evidence_text,
                })

    return rows


def write_snapshot_csv(
    snapshot: dict,
    export_type: str,
    output: str | Path,
) -> int:
    """Analyze a snapshot and write a CSV without overwriting existing files."""
    result = analyze_snapshot(snapshot)
    rows = export_rows(result, export_type)
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=EXPORT_FIELDS[export_type],
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)
