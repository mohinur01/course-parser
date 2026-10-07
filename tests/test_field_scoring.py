# Tests for field delivery scores that distinguish missing records from unknown source
# answers.
from __future__ import annotations

import copy
from pathlib import Path

import pytest

import course_parser.field_scoring as scoring
from course_parser.completion_diagnostics import diagnose_course
from course_parser.io import read_json, sha256, verify_files


# Build the source-reviewed synthetic course used as the expected scoring result.
def gold_course():
    return {
        "dataset_page_id": "source_page",
        "gold_course_id": "source_course",
        "header_physical_page": 1,
        "physical_pages": [1],
        "boundary_status": "CONFIRMED_BOUNDARY",
        "continuation_status": "NONE",
        "description_status": "COMPLETE_DESCRIPTION",
        "raw_course_block": "21. LATIN\nReadings.",
        **{
            name: {"raw": raw, "status": "OBSERVED_EXACT"}
            for name, raw in (
                ("code", "21."),
                ("title", "LATIN"),
                ("description", "Readings."),
                ("credits", "3 credits"),
                ("prerequisites", "Latin 1."),
            )
        },
    }


# Score a possible course boundary from independent source cues while preserving continuation
# conflicts and review reasons.
def score(gold, actual, state="RECORD_EMITTED"):
    row = diagnose_course(gold, actual, pipeline_state=state)
    return scoring.score_course(row, gold)


# Check that end to end average keeps missing course while conditional score excludes it.
def test_end_to_end_average_keeps_missing_course_while_conditional_score_excludes_it():
    gold = gold_course()
    present = score(gold, copy.deepcopy(gold))
    missing = score(gold, None, "ENTRY_WITHHELD")
    result = scoring.aggregate([present, missing])
    assert result["five_field_average"]["pooled_correct"] == 5
    assert result["five_field_average"]["pooled_source_eligible"] == 10
    assert result["five_field_average"]["micro_rate"] == 0.5
    assert result["five_field_average"]["macro_mean_field_rate"] == 0.5
    assert result["field_metrics"]["title"]["conditional_rate"] == 1.0
    assert result["field_metrics"]["title"]["end_to_end_rate"] == 0.5
    assert missing["items"]["title"]["judgment_status"] == "UPSTREAM_NOT_ASSESSED"
    assert missing["items"]["title"]["earned"] == 0
    assert result["strict_completion"]["core"] == {"correct": 1, "source_eligible": 2}


# Leave an unknown source field unscored instead of calling it correct or incorrect.
def test_source_unresolved_field_not_scored_as_negative_or_correct():
    gold = gold_course()
    gold["credits"]["status"] = "SOURCE_UNREADABLE"
    scored = score(gold, copy.deepcopy(gold))
    assert scored["items"]["credits"]["earned"] is None
    assert scored["exact_field_score"]["earned"] == 4
    assert scored["exact_field_score"]["source_eligible"] == 4
    result = scoring.aggregate([scored])
    assert result["field_metrics"]["credits"]["end_to_end_rate"] is None
    assert result["field_metrics"]["credits"]["source_unresolved"] == 1
    assert result["five_field_average"]["macro_fields_available"] == 4


# A withheld empty field must not earn credit for a field confirmed absent in the source.
def test_null_abstention_earns_no_absence_point():
    gold = gold_course()
    gold["credits"] = {"raw": None, "status": "ABSENT_IN_SOURCE"}
    actual = copy.deepcopy(gold)
    actual["credits"]["status"] = "NOT_RECOVERED"
    assert score(gold, actual)["items"]["credits"]["earned"] == 0
    assert score(gold, copy.deepcopy(gold))["items"]["credits"]["earned"] == 1


# Check that partial description equal value does not earn resolved field point.
def test_partial_description_equal_value_does_not_earn_resolved_field_point():
    gold = gold_course()
    actual = copy.deepcopy(gold)
    actual["description"]["status"] = "SAFE_PARTIAL"
    actual["description_status"] = "PARTIAL_DESCRIPTION"
    scored = score(gold, actual)
    assert scored["items"]["description"]["earned"] == 0
    assert scored["complete_exact_description"]["earned"] == 0
    assert scored["strict_completion"]["core"]["correct"] is False


# Check that exact resolved description does not certify description completeness.
def test_exact_resolved_description_does_not_certify_description_completeness():
    gold = gold_course()
    actual = copy.deepcopy(gold)
    actual["description_status"] = "PARTIAL_DESCRIPTION"
    scored = score(gold, actual)
    assert scored["items"]["description"]["earned"] == 1
    assert scored["complete_exact_description"]["earned"] == 0
    assert scored["strict_completion"]["core"]["correct"] is False


# Do not invent a total grade or award points for source boundaries that were never reviewed.
def test_no_invented_eight_point_grade_or_separate_boundary_points():
    gold = gold_course()
    scored = score(gold, copy.deepcopy(gold))
    assert scored["eight_item_panel_coverage"]["not_source_annotated"] == 2
    assert scored["eight_item_panel_coverage"]["source_eligible"] == 6
    assert scored["items"]["start_boundary"]["earned"] is None
    assert scored["items"]["end_boundary"]["earned"] is None
    assert scored["confirmed_owned_extent"]["earned"] == 1
    assert scored["exact_field_score"]["earned"] == 5


# Check that required continuation is visible despite many easy none statuses.
def test_required_continuation_is_visible_despite_many_easy_none_statuses():
    gold = gold_course()
    ordinary = score(gold, copy.deepcopy(gold))
    continued = copy.deepcopy(gold)
    continued["physical_pages"] = [1, 2]
    continued["continuation_status"] = "CONFIRMED_CROSS_PAGE"
    absent = score(continued, None, "ENTRY_WITHHELD")
    result = scoring.aggregate([ordinary] * 17 + [absent])
    assert result["field_metrics"]["continuation"]["correct_delivered"] == 17
    assert result["field_metrics"]["continuation"]["source_eligible"] == 18
    assert result["required_continuation_status"]["source_eligible"] == 1
    assert result["required_continuation_status"]["end_to_end_rate"] == 0


# Check that macro and micro averages have explicit different denominators.
def test_macro_and_micro_averages_have_explicit_different_denominators():
    gold = gold_course()
    complete = score(gold, copy.deepcopy(gold))
    partial_source = copy.deepcopy(gold)
    partial_source["credits"]["status"] = "SOURCE_UNREADABLE"
    partial_source["prerequisites"]["status"] = "SOURCE_UNREADABLE"
    missing = score(partial_source, None, "NO_ALIGNED_HEADER")
    result = scoring.aggregate([complete, missing])["five_field_average"]
    assert result["micro_rate"] == 5 / 8
    assert result["macro_mean_field_rate"] == 0.7
    assert result["macro_fields_available"] == 5


# Check that empty cohort has no fake perfect average.
def test_empty_cohort_has_no_fake_perfect_average():
    result = scoring.aggregate([])
    assert result["five_field_average"]["micro_rate"] is None
    assert result["five_field_average"]["macro_mean_field_rate"] is None
    assert result["five_field_average"]["macro_fields_available"] == 0


# Reject a reserved path before opening its manifest or looking up source paths.
def test_reserved_path_refused_before_manifest_open_or_source_path_lookup(monkeypatch, tmp_path):
    # Fail the test if prohibited data is opened for hashing.
    def no_hash(_):
        pytest.fail("reserved path reached file hashing")

    original_resolve = Path.resolve

    # Resolve allowed paths and fail if a prohibited source location is accessed.
    def guarded_resolve(path, *args, **kwargs):
        if "heldout-review" in path.parts:
            pytest.fail("named reserved path reached a filesystem lookup")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(scoring, "sha256", no_hash)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    forbidden = scoring.WORKSPACE / "artifacts/validation-transfer-v1/heldout-review"
    with pytest.raises(ValueError, match="reserved cohort access is forbidden"):
        scoring.enforce_policy(forbidden, tmp_path / "result")


# Reject an unregistered evaluation sample before opening its manifest.
def test_unknown_cohort_refused_before_opening_its_manifest(monkeypatch, tmp_path):
    # Fail the test if prohibited data is opened for hashing.
    def no_hash(_):
        pytest.fail("unregistered cohort reached an input manifest")

    monkeypatch.setattr(scoring, "sha256", no_hash)
    with pytest.raises(ValueError, match="not a registered"):
        scoring.enforce_policy(tmp_path / "unregistered", tmp_path / "result")


# Apply the reserved-data guard to output paths as well as inputs.
def test_reserved_output_also_forbidden(tmp_path):
    policy = read_json(scoring.POLICY_PATH)
    evaluation = (
        scoring.WORKSPACE / policy["approved_exposed_evaluations"]["original-current"]["path"]
    )
    forbidden_output = scoring.WORKSPACE / "artifacts/validation-transfer-v1/heldout-review/results"
    with pytest.raises(ValueError, match="reserved cohort access is forbidden"):
        scoring.enforce_policy(evaluation, forbidden_output)


# Check that phase two cannot open seal even if policy is modified.
def test_phase_two_cannot_open_seal_even_if_policy_is_modified(monkeypatch, tmp_path):
    policy = read_json(scoring.POLICY_PATH)
    policy["reserved_cohort_access"] = "OPEN"
    monkeypatch.setattr(scoring, "read_json", lambda _: policy)
    with pytest.raises(ValueError, match="18-phase reserved-cohort seal"):
        scoring.enforce_policy(tmp_path / "input", tmp_path / "result")


# Reproduce the recorded field scores and strict completion score for the exposed
# fifteen-course subset.
def test_current_exposed_fifteen_field_scores_and_strict_score(tmp_path):
    evaluation = scoring.WORKSPACE / "artifacts/validation-transfer-v1/release/original-evaluation"
    before_sha = sha256(evaluation / "evaluation_manifest.json")
    release = scoring.write_field_scores(evaluation, tmp_path / "result")
    diagnostic = release["summary"]["by_partition"]["VALIDATION"]
    assert diagnostic["strict_completion"]["core"] == {"correct": 10, "source_eligible": 15}
    assert diagnostic["strict_completion"]["all_fields"] == {"correct": 0, "source_eligible": 15}
    assert {
        name: diagnostic["field_metrics"][name]["correct_delivered"] for name in scoring.FIELDS
    } == {
        "code": 12,
        "title": 12,
        "description": 11,
        "credits": 10,
        "prerequisites": 1,
    }
    assert diagnostic["five_field_average"]["pooled_correct"] == 46
    assert diagnostic["five_field_average"]["pooled_source_eligible"] == 75
    assert diagnostic["required_continuation_status"]["end_to_end_rate"] == 0
    assert diagnostic["field_metrics"]["code"]["end_to_end_rate"] == 12 / 15
    assert diagnostic["field_metrics"]["code"]["conditional_rate"] == 12 / 13
    assert diagnostic["field_metrics"]["start_boundary"]["not_source_annotated"] == 15
    assert sha256(evaluation / "evaluation_manifest.json") == before_sha
    assert release["reserved_cohort_opened"] is False
    verify_files(tmp_path / "result", release["file_sha256"])
    with pytest.raises(FileExistsError):
        scoring.write_field_scores(evaluation, tmp_path / "result")
