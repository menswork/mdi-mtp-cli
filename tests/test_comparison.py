import pytest

from mtp_cli.comparison import compare_snapshots


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


def snapshot(trainees, collected_at):
    return {
        "schema_version": 1,
        "collected_at": collected_at,
        "trainees": trainees,
    }


OLDER = "2026-10-01T12:00:00Z"
NEWER = "2026-11-04T12:00:00Z"


def test_detects_added_missing_and_changed_records():
    old = snapshot([
        trainee(1),
        trainee(2),
        trainee(3),
    ], OLDER)

    new = snapshot([
        trainee(1, mentor="Mentor B"),
        trainee(2, status="Completed program"),
        trainee(4),
    ], NEWER)

    result = compare_snapshots(old, new)

    assert result["totals"] == {
        "before": 3, "after": 3, "change": 0
    }
    assert result["records"]["added_count"] == 1
    assert result["records"]["added"][0]["id"] == 4
    assert result["records"]["missing_from_newer_count"] == 1
    assert result["records"]["missing_from_newer"][0]["id"] == 3
    assert result["records"]["changed_count"] == 2

    changes = {
        item["id"]: item["changes"]
        for item in result["records"]["changed"]
    }
    assert changes[1]["mentor"]["after"] == "Mentor B"
    assert changes[2]["status"]["after"] == "Completed program"


def test_detects_newly_completed_and_reversed_milestones():
    old = snapshot([
        trainee(1, milestones=[
            {"milestone": "Lead a group", "done": True},
            {"milestone": "Give a talk", "done": True},
        ]),
    ], OLDER)

    new = snapshot([
        trainee(1, milestones=[
            {"milestone": "Lead a group", "done": True},
            {"milestone": "Give a talk", "done": False},
            {"milestone": "Organize event", "done": True},
        ]),
    ], NEWER)

    result = compare_snapshots(old, new)
    changes = result["records"]["changed"][0]["changes"]

    assert changes["milestones"]["newly_completed"] == ["organize event"]
    assert changes["milestones"]["no_longer_completed"] == ["give a talk"]
    assert result["milestone_progress"]["completed_change"] == 0
    assert result["milestone_progress"]["total_change"] == 1


def test_inactivity_uses_each_snapshots_own_reference_date():
    old = snapshot([
        trainee(1, latest_date="2026-09-20"),
    ], OLDER)

    new = snapshot([
        trainee(1, latest_date="2026-09-20"),
    ], NEWER)

    result = compare_snapshots(old, new)

    assert result["records"]["changed_count"] == 0
    assert result["records"]["inactivity_changed_count"] == 1

    change = result["records"]["inactivity_changed"][0]
    assert change["before"]["days_since_activity"] == 11
    assert change["after"]["days_since_activity"] == 45
    assert change["before"]["band"] == "Attention"
    assert change["after"]["band"] == "Stalled"


def test_duplicate_ids_are_not_matched():
    old = snapshot([
        trainee(1),
        trainee(1, name="Second record"),
        trainee(2),
    ], OLDER)

    new = snapshot([
        trainee(1),
        trainee(2),
    ], NEWER)

    result = compare_snapshots(old, new)

    assert result["ambiguous_ids"]["older"] == ["1"]
    assert result["records"]["added_count"] == 1
    assert result["records"]["added"][0]["id"] == 1
    assert result["records"]["missing_from_newer_count"] == 0


def test_records_without_ids_are_counted_not_name_matched():
    old = snapshot([
        trainee(None, name="Same Name"),
    ], OLDER)

    new = snapshot([
        trainee(None, name="Same Name"),
    ], NEWER)

    result = compare_snapshots(old, new)

    assert result["records_without_ids"] == {
        "older": 1,
        "newer": 1,
    }
    assert result["records"]["changed_count"] == 0
    assert result["records"]["added_count"] == 0


def test_status_normalization_avoids_false_change():
    old = snapshot([
        trainee(1, status="no data"),
    ], OLDER)

    new = snapshot([
        trainee(1, status="Other / No Data"),
    ], NEWER)

    result = compare_snapshots(old, new)

    assert result["records"]["changed_count"] == 0


def test_invalid_snapshot_is_rejected():
    with pytest.raises(ValueError, match="schema version"):
        compare_snapshots(
            {"schema_version": 2},
            snapshot([], NEWER),
        )


def test_milestone_identity_uses_goal_id_for_duplicate_names():
    old = snapshot([
        trainee(1, milestones=[
            {"goalId": 10, "milestone": "Attend workshop", "done": True},
            {"goalId": 11, "milestone": "Attend workshop", "done": False},
        ]),
    ], OLDER)

    new = snapshot([
        trainee(1, milestones=[
            {"goalId": 10, "milestone": "Attend workshop", "done": True},
            {"goalId": 11, "milestone": "Attend workshop", "done": True},
        ]),
    ], NEWER)

    result = compare_snapshots(old, new)
    changes = result["records"]["changed"][0]["changes"]

    assert changes["milestones"]["newly_completed"] == ["attend workshop"]
    assert changes["milestones"]["no_longer_completed"] == []


def test_renamed_milestone_with_same_goal_id_is_not_a_new_completion():
    old = snapshot([
        trainee(1, milestones=[
            {"goalId": 42, "milestone": "Attend workshop", "done": True},
        ]),
    ], OLDER)

    new = snapshot([
        trainee(1, milestones=[
            {"goalId": 42, "milestone": "Participate in workshop", "done": True},
        ]),
    ], NEWER)

    result = compare_snapshots(old, new)

    assert result["records"]["changed_count"] == 0


def test_elapsed_days_without_band_change_is_not_reported():
    old = snapshot([
        trainee(1, latest_date="2026-09-20"),
    ], "2026-10-01T12:00:00Z")

    new = snapshot([
        trainee(1, latest_date="2026-09-20"),
    ], "2026-10-06T12:00:00Z")

    result = compare_snapshots(old, new)

    assert result["records"]["changed_count"] == 0
    assert result["records"]["inactivity_changed_count"] == 0
    assert result["elapsed_days"] == 5


def test_activity_date_change_within_same_band_is_not_inactivity_change():
    old = snapshot([
        trainee(1, latest_date="2026-09-28"),
    ], "2026-10-01T12:00:00Z")

    new = snapshot([
        trainee(1, latest_date="2026-10-03"),
    ], "2026-10-06T12:00:00Z")

    result = compare_snapshots(old, new)

    assert result["records"]["inactivity_changed_count"] == 0
    assert result["records"]["changed_count"] == 1

    changes = result["records"]["changed"][0]["changes"]
    assert changes["latest_date"] == {
        "before": "2026-09-28",
        "after": "2026-10-03",
    }


def test_inactivity_change_when_trainee_leaves_in_progress():
    old = snapshot([
        trainee(1, status="In progress", latest_date="2026-09-25"),
    ], "2026-10-01T12:00:00Z")

    new = snapshot([
        trainee(1, status="Completed program", latest_date="2026-09-25"),
    ], "2026-10-06T12:00:00Z")

    result = compare_snapshots(old, new)

    assert result["records"]["inactivity_changed_count"] == 1

    change = result["records"]["inactivity_changed"][0]
    assert change["before"]["band"] == "Current"
    assert change["after"] is None


def test_inactivity_change_when_trainee_enters_in_progress():
    old = snapshot([
        trainee(1, status="Not yet started", latest_date="2026-09-25"),
    ], "2026-10-01T12:00:00Z")

    new = snapshot([
        trainee(1, status="In progress", latest_date="2026-09-25"),
    ], "2026-10-06T12:00:00Z")

    result = compare_snapshots(old, new)

    assert result["records"]["inactivity_changed_count"] == 1

    change = result["records"]["inactivity_changed"][0]
    assert change["before"] is None
    assert change["after"]["band"] == "Attention"


def test_added_and_missing_records_use_name_fallback():
    old_record = {
        **trainee(1),
        "name": None,
        "firstName": "Alex",
        "lastName": "Example",
    }
    new_record = {
        **trainee(2),
        "name": 12345,
        "firstName": "Jordan",
        "lastName": "Sample",
    }

    result = compare_snapshots(
        snapshot([old_record], OLDER),
        snapshot([new_record], NEWER),
    )

    assert result["records"]["missing_from_newer"][0]["name"] == "Alex Example"
    assert result["records"]["added"][0]["name"] == "Jordan Sample"


def test_changed_record_uses_valid_name_fallback():
    old_record = {
        **trainee(1, status="Not yet started"),
        "name": "Old Display Name",
    }
    new_record = {
        **trainee(1, status="In progress"),
        "name": ["Invalid"],
        "firstName": "Alex",
        "lastName": "Example",
    }

    result = compare_snapshots(
        snapshot([old_record], OLDER),
        snapshot([new_record], NEWER),
    )

    assert result["records"]["changed_count"] == 1
    assert result["records"]["changed"][0]["name"] == "Alex Example"
    assert result["records"]["inactivity_changed"][0]["name"] == "Alex Example"


def test_changed_record_falls_back_to_older_valid_name():
    old_record = trainee(1, status="Not yet started")
    new_record = {
        **trainee(1, status="In progress"),
        "name": {"invalid": "name"},
    }

    result = compare_snapshots(
        snapshot([old_record], OLDER),
        snapshot([new_record], NEWER),
    )

    assert result["records"]["changed"][0]["name"] == "Trainee 1"
    assert result["records"]["inactivity_changed"][0]["name"] == "Trainee 1"


def test_comparison_does_not_load_duplicate_confirmations(
    monkeypatch,
):
    from mtp_cli import analysis

    def unexpected_load(*args, **kwargs):
        raise AssertionError(
            "Comparison must not load duplicate confirmations"
        )

    monkeypatch.setattr(
        analysis,
        "load_duplicate_confirmations",
        unexpected_load,
    )

    result = compare_snapshots(
        snapshot([trainee(1)], OLDER),
        snapshot([trainee(1)], NEWER),
    )

    assert result["totals"]["before"] == 1
    assert result["totals"]["after"] == 1
