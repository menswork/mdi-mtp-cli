import csv

import pytest

from mtp_cli.exports import export_rows, write_snapshot_csv
from mtp_cli.analysis import analyze_snapshot


def trainee(
    trainee_id,
    *,
    name=None,
    status="In progress",
    latest_date="2026-11-04",
    mentor="Mentor A",
    division="Division A",
    team="Team A",
    milestones=None,
):
    return {
        "id": trainee_id,
        "name": name or f"Trainee {trainee_id}",
        "status": status,
        "latestDate": latest_date,
        "mentor": mentor,
        "division": division,
        "team": team,
        "milestones": milestones or [],
    }


def snapshot(trainees):
    return {
        "schema_version": 1,
        "collected_at": "2026-11-04T23:18:43Z",
        "trainees": trainees,
    }


def test_inactivity_export_has_one_row_per_active_trainee():
    data = snapshot([
        trainee(1, latest_date="2026-11-04"),
        trainee(2, latest_date="2026-08-01", mentor="Mentor B"),
        trainee(3, status="Completed program"),
        trainee(4, latest_date=None),
    ])

    result = analyze_snapshot(data)
    rows = export_rows(result, "inactivity")

    assert len(rows) == 3
    assert {row["id"] for row in rows} == {1, 2, 4}
    assert {row["inactivity_band"] for row in rows} == {
        "Current", "Extended", "Unknown"
    }


def test_mentor_export_includes_all_inactivity_bands():
    result = analyze_snapshot(snapshot([
        trainee(1, mentor="Mentor A"),
        trainee(2, mentor="Mentor A", latest_date="2026-08-01"),
        trainee(3, mentor="Mentor B"),
    ]))

    rows = export_rows(result, "mentors")
    by_name = {row["mentor"]: row for row in rows}

    assert by_name["Mentor A"]["total_in_progress"] == 2
    assert by_name["Mentor A"]["inactivity_current"] == 1
    assert by_name["Mentor A"]["inactivity_extended"] == 1
    assert by_name["Mentor B"]["total_in_progress"] == 1
    assert sum(
        by_name["Mentor A"][f"inactivity_{band.lower()}"]
        for band in (
            "Current", "Attention", "Stalled",
            "Extended", "Unknown", "Future",
        )
    ) == 2


def test_duplicate_export_flattens_groups_to_one_row_per_record():
    records = [
        trainee(
            1,
            name="Example Person",
            milestones=[
                {"milestone": f"Milestone {n}", "done": True}
                for n in range(4)
            ],
        ),
        trainee(
            2,
            name=" example person ",
            milestones=[
                {"milestone": f"Milestone {n}", "done": True}
                for n in range(4)
            ],
        ),
    ]

    result = analyze_snapshot(snapshot(records))
    rows = export_rows(result, "duplicates")

    assert len(rows) == 2
    assert {row["record_id"] for row in rows} == {1, 2}
    assert all(row["group_name"] == "Example Person" for row in rows)
    assert all(row["group_confidence"] == "High" for row in rows)
    assert all(row["lowest_pair_confidence"] == "High" for row in rows)
    assert all("shared completed milestone" in row["comparison_evidence"]
               for row in rows)


def test_export_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown export type"):
        export_rows({}, "unknown")


def test_csv_writer_creates_file_and_refuses_overwrite(tmp_path):
    data = snapshot([trainee(1)])
    output = tmp_path / "nested" / "inactivity.csv"

    count = write_snapshot_csv(data, "inactivity", output)

    assert count == 1
    with output.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["name"] == "Trainee 1"

    with pytest.raises(FileExistsError):
        write_snapshot_csv(data, "inactivity", output)


def test_duplicate_export_preserves_mixed_pair_confidence():
    records = [
        trainee(
            1,
            name="Alex Example",
            division="Shared Division",
            team="Shared Team",
            milestones=[
                {"milestone": f"Goal {n}", "done": True}
                for n in range(4)
            ],
        ),
        trainee(
            2,
            name="Example, Alex",
            division="Shared Division",
            team="Shared Team",
            milestones=[
                {"milestone": f"Goal {n}", "done": True}
                for n in range(4)
            ],
        ),
        trainee(
            3,
            name="ALEX EXAMPLE",
            division="Other Division",
            team="Other Team",
            milestones=[],
        ),
    ]

    result = analyze_snapshot(snapshot(records))
    rows = export_rows(result, "duplicates")

    assert len(rows) == 3
    assert all(row["group_confidence"] == "High" for row in rows)
    assert all(row["lowest_pair_confidence"] == "Low" for row in rows)
    assert all("High:" in row["comparison_evidence"] for row in rows)
    assert all("Low:" in row["comparison_evidence"] for row in rows)
