import datetime as dt

import pytest

from mtp_cli.analysis import analyze_snapshot


REFERENCE_DATE = dt.date(2026, 11, 4)


def trainee(
    trainee_id,
    *,
    status="In progress",
    days_ago=None,
    division="Division A",
    team="Team A",
    mentor="Mentor A",
    milestones=None,
):
    latest_date = None
    if days_ago is not None:
        latest_date = (
            REFERENCE_DATE - dt.timedelta(days=days_ago)
        ).isoformat()

    return {
        "id": trainee_id,
        "name": f"Trainee {trainee_id}",
        "status": status,
        "division": division,
        "team": team,
        "mentor": mentor,
        "latestDate": latest_date,
        "milestones": milestones or [],
    }


def snapshot(trainees):
    return {
        "schema_version": 1,
        "collected_at": "2026-11-04T23:18:43Z",
        "trainees": trainees,
    }


def test_inactivity_boundaries():
    records = [
        trainee(1, days_ago=0),
        trainee(2, days_ago=7),
        trainee(3, days_ago=8),
        trainee(4, days_ago=21),
        trainee(5, days_ago=22),
        trainee(6, days_ago=89),
        trainee(7, days_ago=90),
        trainee(8, days_ago=None),
        {
            **trainee(9),
            "latestDate": "not-a-date",
        },
        {
            **trainee(10),
            "latestDate": "2026-11-05",
        },
    ]

    result = analyze_snapshot(snapshot(records))

    assert result["inactivity_counts"] == {
        "Current": 2,
        "Attention": 2,
        "Stalled": 2,
        "Extended": 1,
        "Unknown": 2,
        "Future": 1,
    }

    assert result["data_quality"]["missing_latest_date"] == 1
    assert result["data_quality"]["malformed_latest_date"] == 1
    assert result["data_quality"]["future_latest_date"] == 1



@pytest.mark.parametrize(
    ("days_inactive", "expected_band"),
    [
        (0, "Current"),
        (7, "Current"),
        (8, "Attention"),
        (21, "Attention"),
        (22, "Stalled"),
        (89, "Stalled"),
        (90, "Extended"),
        (365, "Extended"),
        (-1, "Future"),
        (None, "Unknown"),
    ],
)
def test_inactivity_band_thresholds(days_inactive, expected_band):
    result = analyze_snapshot(snapshot([
        trainee(1, days_ago=days_inactive),
    ]))

    assert result["inactivity_counts"][expected_band] == 1

def test_division_and_team_milestone_totals_are_independent():
    records = [
        trainee(
            1,
            division="Shared",
            team="Shared",
            milestones=[
                {"done": True},
                {"done": False},
            ],
        ),
        trainee(
            2,
            division="Shared",
            team="Different Team",
            milestones=[
                {"done": True},
            ],
        ),
        trainee(
            3,
            division="Different Division",
            team="Shared",
            milestones=[
                {"done": False},
                {"done": False},
                {"done": False},
            ],
        ),
    ]

    result = analyze_snapshot(snapshot(records))

    assert result["division_summary"]["Shared"]["milestones_done"] == 2
    assert result["division_summary"]["Shared"]["milestones_total"] == 3

    assert result["team_summary"]["Shared"]["milestones_done"] == 1
    assert result["team_summary"]["Shared"]["milestones_total"] == 5


def test_no_data_status_is_normalized():
    result = analyze_snapshot(snapshot([
        trainee(1, status="no data"),
        trainee(2, status="Other / No Data"),
    ]))

    assert result["status_counts"] == {"Other / No Data": 2}


def test_attention_lists_and_mentor_summary():
    records = [
        trainee(1, days_ago=95, mentor="Mentor A"),
        trainee(2, days_ago=15, mentor="Mentor A"),
        trainee(3, days_ago=10, mentor="Mentor B"),
        trainee(4, days_ago=95, mentor=None),
    ]

    result = analyze_snapshot(snapshot(records))

    assert [item["id"] for item in result["inactivity_trainees"]["Extended"]] == [1, 4]
    assert result["inactivity_trainees"]["Attention"][0]["id"] == 2

    mentor_a = result["mentor_attention_summary"]["Mentor A"]
    assert mentor_a["total_in_progress"] == 2
    assert mentor_a["inactivity"]["Extended"] == 1
    assert mentor_a["inactivity"]["Attention"] == 1

    unassigned = result["mentor_attention_summary"]["Unassigned"]
    assert unassigned["total_in_progress"] == 1
    assert unassigned["inactivity"]["Extended"] == 1


def test_duplicate_ids_are_counted():
    result = analyze_snapshot(snapshot([
        trainee(1),
        trainee(1),
        trainee(2),
    ]))

    assert result["data_quality"]["duplicate_trainee_ids"] == 1


def test_invalid_snapshot_is_rejected():
    with pytest.raises(ValueError, match="schema version"):
        analyze_snapshot({"schema_version": 2})

    with pytest.raises(ValueError, match="collected_at"):
        analyze_snapshot({
            "schema_version": 1,
            "collected_at": "not-a-timestamp",
            "trainees": [],
        })

    with pytest.raises(ValueError, match="trainees"):
        analyze_snapshot({
            "schema_version": 1,
            "collected_at": "2026-11-04T23:18:43Z",
            "trainees": {},
        })


def test_duplicate_review_marks_manually_confirmed_pair(tmp_path, monkeypatch):
    import json
    from mtp_cli import analysis

    confirmation_file = tmp_path / "duplicate-confirmations.json"
    confirmation_file.write_text(
        json.dumps({
            "confirmed_pairs": [{
                "ids": [
                    "/u/synthetic-trainee-003/posts/1003",
                    "/u/synthetic-trainee-004/posts/1004"
                ],
                "reason": "Manually confirmed duplicate"
            }]
        }),
        encoding="utf-8"
    )
    monkeypatch.setattr(
        analysis, "DEFAULT_CONFIRMATIONS_PATH", confirmation_file
    )

    first = {
        **trainee(1, status="Completed program",
                  division="West Bay", team="Rising Phoenix",
                  mentor="Beale", milestones=[
                      {"milestone": "Lead Fun", "done": True},
                      {"milestone": "Teach", "done": True},
                  ]),
        "name": "Example, Alex",
        "id": "/u/synthetic-trainee-003/posts/1003",
    }
    second = {
        **trainee(2, status="Left before completing",
                  division="West Bay", team="Ocean Kings",
                  mentor=None, milestones=[
                      {"milestone": "Lead Fun", "done": True},
                  ]),
        "name": "Alex Example",
        "id": "/u/synthetic-trainee-004/posts/1004",
    }

    result = analyze_snapshot(snapshot([first, second]))
    review = result["duplicate_review"]

    assert review["repeated_name_groups"] == 1
    assert review["records_in_groups"] == 2
    group = review["groups"][0]
    assert group["confidence"] == "Confirmed"
    assert group["record_count"] == 2
    assert "Manually confirmed duplicate" in (
        group["comparisons"][0]["evidence"]
    )


def test_duplicate_review_confidence_uses_record_evidence():
    records = [
        {
            **trainee(1, division="Same Division", team="Same Team"),
            "name": "Example Person",
            "id": "/u/synthetic-trainee-005/posts/1005",
            "milestones": [
                {"milestone": f"Milestone {n}", "done": True}
                for n in range(4)
            ],
        },
        {
            **trainee(2, division="Same Division", team="Same Team"),
            "name": " example   person ",
            "id": "/u/synthetic-trainee-006/posts/1006",
            "milestones": [
                {"milestone": f"Milestone {n}", "done": True}
                for n in range(4)
            ],
        },
        {
            **trainee(3, division="Other Division", team="Other Team"),
            "name": "EXAMPLE PERSON",
            "id": "/u/synthetic-trainee-001/posts/1001",
            "milestones": [],
        },
    ]

    review = analyze_snapshot(snapshot(records))["duplicate_review"]
    assert review["repeated_name_groups"] == 1
    assert review["records_in_groups"] == 3
    group = review["groups"][0]

    # The strongest pair determines the group summary, while the
    # weakest pair remains visible to prevent overinterpretation.
    assert group["confidence"] == "High"
    assert group["lowest_pair_confidence"] == "Low"
    assert len(group["records"]) == 3

    pair_confidences = [
        comparison["confidence"]
        for comparison in group["comparisons"]
    ]
    assert pair_confidences.count("High") == 1
    assert pair_confidences.count("Low") == 2

def test_duplicate_review_low_confidence_for_name_only_match():
    records = [
        {**trainee(1, division="Division A", team="Team A"),
         "name": "Jordan Example", "id": "/u/synthetic-trainee-007/posts/1007"},
        {**trainee(2, division="Division B", team="Team B"),
         "name": "Jordan Example", "id": "/u/synthetic-trainee-010/posts/1010"},
    ]

    review = analyze_snapshot(snapshot(records))["duplicate_review"]
    assert review["groups"][0]["confidence"] == "Low"


def test_duplicate_review_does_not_modify_snapshot():
    import copy

    records = [
        {**trainee(1), "name": "Same Name"},
        {**trainee(2), "name": "Same Name"},
    ]
    original = copy.deepcopy(records)

    analyze_snapshot(snapshot(records))

    assert records == original


def test_duplicate_review_ignores_blank_names_and_unique_names():
    records = [
        {**trainee(1), "name": ""},
        {**trainee(2), "name": "   "},
        {**trainee(3), "name": "Only Person"},
    ]

    review = analyze_snapshot(snapshot(records))["duplicate_review"]
    assert review["repeated_name_groups"] == 0
    assert review["records_in_groups"] == 0
    assert review["groups"] == []


def test_manual_duplicate_confirmation_does_not_apply_to_other_records():
    first = {
        **trainee(
            1,
            status="Completed program",
            division="West Bay",
            team="Rising Phoenix",
            mentor="Beale",
            milestones=[
                {"milestone": "Lead Fun", "done": True},
            ],
        ),
        "name": "Example, Alex",
        "id": "/u/synthetic-trainee-002/posts/1002",
    }
    second = {
        **trainee(
            2,
            status="Left before completing",
            division="Atlantic",
            team="Other Team",
            mentor=None,
            milestones=[],
        ),
        "name": "Alex Example",
        "id": "/u/synthetic-trainee-008/posts/1008",
    }

    result = analyze_snapshot(snapshot([first, second]))
    review = result["duplicate_review"]

    assert review["repeated_name_groups"] == 1
    assert review["records_in_groups"] == 2

    group = review["groups"][0]
    assert group["confidence"] == "Low"

    evidence = group["comparisons"][0]["evidence"]
    assert not any(
        item.startswith("User-confirmed:")
        for item in evidence
    )


def test_latest_date_rejects_valid_date_with_trailing_text():
    records = [
        {
            **trainee(1),
            "latestDate": "2026-11-04garbage",
        },
        {
            **trainee(2),
            "latestDate": "2026-11-04T12:00:00",
        },
    ]

    result = analyze_snapshot(snapshot(records))

    assert result["data_quality"]["malformed_latest_date"] == 2
    assert result["data_quality"]["missing_latest_date"] == 0
    assert result["inactivity_counts"]["Unknown"] == 2


def test_duplicate_id_detection_handles_unhashable_ids():
    records = [
        {**trainee(1), "id": ["unexpected", "list"]},
        {**trainee(2), "id": ["unexpected", "list"]},
        trainee(3),
    ]

    result = analyze_snapshot(snapshot(records))

    assert result["data_quality"]["duplicate_trainee_ids"] == 1
    assert result["inactivity_trainees"]["Unknown"][0]["id"] == [
        "unexpected", "list"
    ]


def test_snapshot_must_be_a_json_object():
    with pytest.raises(ValueError, match="JSON object"):
        analyze_snapshot([])

    with pytest.raises(ValueError, match="JSON object"):
        analyze_snapshot(None)


def test_snapshot_rejects_non_object_trainee():
    with pytest.raises(ValueError, match="trainee at index 0"):
        analyze_snapshot(snapshot(["not a trainee"]))


def test_snapshot_rejects_invalid_milestone_structure():
    with pytest.raises(ValueError, match="milestones must be a list"):
        analyze_snapshot(snapshot([
            {**trainee(1), "milestones": "not a list"},
        ]))

    with pytest.raises(ValueError, match="milestone 0 must be an object"):
        analyze_snapshot(snapshot([
            {**trainee(1), "milestones": ["not a milestone"]},
        ]))


def test_snapshot_rejects_timestamp_without_timezone():
    with pytest.raises(ValueError, match="collected_at"):
        analyze_snapshot({
            "schema_version": 1,
            "collected_at": "2026-11-04T23:18:43",
            "trainees": [],
        })


def test_duplicate_confirmations_missing_file_returns_empty(tmp_path):
    from mtp_cli.analysis import load_duplicate_confirmations

    path = tmp_path / "missing-confirmations.json"
    assert load_duplicate_confirmations(path) == {}


def test_duplicate_confirmations_valid_file(tmp_path):
    import json
    from mtp_cli.analysis import load_duplicate_confirmations

    path = tmp_path / "confirmations.json"
    path.write_text(
        json.dumps({
            "confirmed_pairs": [{
                "ids": ["/u/synthetic-trainee-005/posts/1005", "/u/synthetic-trainee-009/posts/1009"],
                "reason": "Verified manually",
            }]
        }),
        encoding="utf-8",
    )

    assert load_duplicate_confirmations(path) == {
        frozenset({
            "/u/synthetic-trainee-005/posts/1005",
            "/u/synthetic-trainee-009/posts/1009",
        }): "Verified manually"
    }


def test_duplicate_confirmations_rejects_invalid_json(tmp_path):
    import pytest
    from mtp_cli.analysis import load_duplicate_confirmations

    path = tmp_path / "invalid.json"
    path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError):
        load_duplicate_confirmations(path)


def test_duplicate_confirmations_rejects_invalid_entries(tmp_path):
    import json
    import pytest
    from mtp_cli.analysis import load_duplicate_confirmations

    invalid_cases = [
        {"confirmed_pairs": "not a list"},
        {"confirmed_pairs": [{"ids": ["only-one"], "reason": "Test"}]},
        {"confirmed_pairs": [{"ids": ["same", "same"], "reason": "Test"}]},
        {"confirmed_pairs": [{"ids": ["one", "two"], "reason": ""}]},
        {
            "confirmed_pairs": [
                {"ids": ["one", "two"], "reason": "First"},
                {"ids": ["two", "one"], "reason": "Second"},
            ]
        },
    ]

    path = tmp_path / "invalid-confirmations.json"

    for data in invalid_cases:
        path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError):
            load_duplicate_confirmations(path)


def test_duplicate_review_ignores_invalid_name_types():
    records = [
        {**trainee(1), "name": 12345},
        {**trainee(2), "name": ["Alex", "Example"]},
        {**trainee(3), "name": {"first": "Alex"}},
        {**trainee(4), "name": "Valid Person"},
    ]

    result = analyze_snapshot(snapshot(records))
    review = result["duplicate_review"]

    assert review["repeated_name_groups"] == 0
    assert review["groups"] == []


def test_duplicate_review_uses_valid_first_and_last_name_fallback():
    records = [
        {
            **trainee(1),
            "name": 12345,
            "firstName": "Alex",
            "lastName": "Example",
        },
        {
            **trainee(2),
            "name": None,
            "firstName": "Example",
            "lastName": "Alex",
        },
    ]

    review = analyze_snapshot(snapshot(records))["duplicate_review"]

    assert review["repeated_name_groups"] == 1
    assert review["records_in_groups"] == 2
    assert review["groups"][0]["name"] == "Alex Example"


def test_inactivity_report_handles_invalid_name_types():
    records = [
        {**trainee(1), "name": 12345},
        {
            **trainee(2),
            "name": ["Invalid"],
            "firstName": "Alex",
            "lastName": "Example",
        },
    ]

    result = analyze_snapshot(snapshot(records))
    names = {
        item["id"]: item["name"]
        for band in result["inactivity_trainees"].values()
        for item in band
    }

    assert names[1] == "Unnamed trainee"
    assert names[2] == "Alex Example"


def test_human_readable_duplicate_review_hides_record_ids(
    tmp_path, capsys,
):
    import argparse
    import json

    from mtp_cli.cli import cmd_analyze_offline

    snapshot = {
        "schema_version": 1,
        "collected_at": "2026-11-04T12:00:00Z",
        "trainees": [
            {
                "id": "/u/synthetic-account/posts/101",
                "name": "Example Trainee",
                "status": "In progress",
                "division": "Example Division",
                "team": "Example Team",
                "mentor": "Example Mentor",
                "milestones": [],
            },
            {
                "id": "/u/synthetic-account/posts/102",
                "name": "Example Trainee",
                "status": "In progress",
                "division": "Example Division",
                "team": "Example Team",
                "mentor": "Example Mentor",
                "milestones": [],
            },
        ],
    }

    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    result = cmd_analyze_offline(
        argparse.Namespace(snapshot=str(path), json=False, details=True)
    )

    output = capsys.readouterr().out

    assert result == 0
    assert "DUPLICATE REVIEW" in output
    assert "Example Division | Example Team" in output
    assert "/u/synthetic-account/posts/" not in output
