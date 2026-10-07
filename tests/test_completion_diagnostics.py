# Tests for explaining incomplete course records without guessing the cause of missing
# evidence.
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from course_parser.completion_diagnostics import (
    diagnose_course,
    load_exposed_evaluation,
    summarize,
    write_diagnostics,
)
from course_parser.io import read_json, sha256, verify_files, write_json

WORKSPACE = Path(__file__).resolve().parents[1]


# Build a synthetic recovered course and its reviewed counterpart for field diagnostics.
@pytest.fixture
def course_pair():
    expected = {
        "dataset_page_id": "test_page",
        "gold_course_id": "gold_1",
        "header_physical_page": 1,
        "physical_pages": [1],
        "boundary_status": "CONFIRMED_BOUNDARY",
        "continuation_status": "NONE",
        "description_status": "COMPLETE_DESCRIPTION",
        "raw_course_block": "21. LATIN\nReadings.",
        **{
            name: {"raw": value, "status": "OBSERVED_EXACT"}
            for name, value in (
                ("code", "21."),
                ("title", "LATIN"),
                ("description", "Readings."),
                ("credits", "3 credits"),
                ("prerequisites", "Prerequisite: Latin 1."),
            )
        },
    }
    return expected, copy.deepcopy(expected)


# Check that metadata failure does not change core definition.
def test_metadata_failure_does_not_change_core_definition(course_pair):
    expected, actual = course_pair
    actual["prerequisites"]["raw"] = "Latin 1."
    row = diagnose_course(expected, actual)
    assert row["completion"]["core"]["correct"] is True
    assert row["completion"]["all_fields"]["failed_criteria"] == ["prerequisites"]
    assert row["completion"]["all_fields"]["correct"] is False


# Check that exact value with partial description is not complete.
def test_exact_value_with_partial_description_is_not_complete(course_pair):
    expected, actual = course_pair
    actual["description"]["status"] = "SAFE_PARTIAL"
    actual["description_status"] = "PARTIAL_DESCRIPTION"
    row = diagnose_course(expected, actual)
    assert row["fields"]["description"]["value_exact"] is True
    assert row["completion"]["core"]["failed_criteria"] == ["description"]
    assert row["completion"]["core"]["correct"] is False
    assert "DESCRIPTION_FIELD_ABSTAINED" in row["fields"]["description"]["reason_codes"]


# Check that exact raw block does not certify boundary or source order.
def test_exact_raw_block_does_not_certify_boundary_or_source_order(course_pair):
    expected, actual = course_pair
    actual["boundary_status"] = "UNRESOLVED_BOUNDARY"
    row = diagnose_course(expected, actual)
    assert row["structure"]["raw_block_exact"]["status"] == "PASS"
    assert row["structure"]["owned_extent"]["status"] == "FAIL"
    assert row["structure"]["owned_extent"]["reason_codes"] == ["BOUNDARY_UNCONFIRMED"]
    for name in ("start_boundary", "end_boundary", "ocr", "reading_order", "source_text_complete"):
        assert row["structure"][name]["status"] == "NOT_ASSESSED"


# Do not count an unknown source answer as a failed completion requirement.
def test_source_unresolved_excluded_from_distance_not_declared_failure(course_pair):
    expected, actual = course_pair
    expected["credits"]["status"] = "SOURCE_UNREADABLE"
    row = diagnose_course(expected, actual)
    assert row["fields"]["credits"]["status"] == "SOURCE_UNRESOLVED"
    assert row["completion"]["all_fields"]["correct"] is None
    assert row["completion"]["all_fields"]["failed_criteria_count"] is None
    assert row["completion"]["core"]["correct"] is True


# Check that null abstention is not source confirmed absence.
def test_null_abstention_is_not_source_confirmed_absence(course_pair):
    expected, actual = course_pair
    expected["credits"] = {"raw": None, "status": "ABSENT_IN_SOURCE"}
    actual["credits"] = {"raw": None, "status": "NOT_RECOVERED"}
    row = diagnose_course(expected, actual)
    assert row["fields"]["credits"]["status"] == "FAIL"
    assert row["completion"]["all_fields"]["failed_criteria"] == ["credits"]


# Check that upstream misses have no field distance and stay in denominator.
@pytest.mark.parametrize(
    "state",
    [
        "ENTRY_WITHHELD",
        "NO_ALIGNED_HEADER",
        "ALIGNMENT_AMBIGUOUS",
        "PARSER_STAGE_FAILED",
    ],
)
def test_upstream_misses_have_no_field_distance_and_stay_in_denominator(course_pair, state):
    expected, _ = course_pair
    row = diagnose_course(expected, None, pipeline_state=state)
    assert row["fields"]["description"]["status"] == "UPSTREAM_NOT_ASSESSED"
    assert row["completion"]["core"]["correct"] is False
    assert row["completion"]["core"]["failed_criteria_count"] is None
    summary = summarize([row])
    assert summary["core"]["eligible"] == 1
    assert sum(summary["core"]["failed_criteria_buckets"].values()) == 0
    assert summary["core"]["not_distance_assessed"] == {state: 1}
    if state in {"ALIGNMENT_AMBIGUOUS", "PARSER_STAGE_FAILED"}:
        assert row["detection"]["status"] == "NOT_ASSESSED"


# Check that overlap is joint and preserves remaining failure.
def test_overlap_is_joint_and_preserves_remaining_failure(course_pair):
    expected, actual = course_pair
    actual["code"]["raw"] = "21"
    identifier_failure = diagnose_course(expected, actual)
    actual = copy.deepcopy(expected)
    actual["description"]["raw"] = "Wrong narrative."
    description_failure = diagnose_course(expected, actual)
    actual = copy.deepcopy(expected)
    actual["boundary_status"] = "UNRESOLVED_BOUNDARY"
    extent_failure = diagnose_course(expected, actual)
    summary = summarize([identifier_failure, description_failure, extent_failure])
    assert summary["confirmed_extent_exact"] == 2
    assert summary["description_value_exact"] == 2
    joint = summary["confirmed_extent_and_description_value_exact"]
    assert joint["count"] == 1
    assert joint["courses"][0]["remaining_core_criteria"] == ["code"]
    assert identifier_failure["fields"]["code"]["reason_codes"] == ["CODE_PUNCTUATION_MISMATCH"]


# Reject protected or unapproved evaluations before reading their answers.
@pytest.mark.parametrize(
    "change",
    [
        {"scope": "PROTECTED_TEST"},
        {"protected_evaluation_opened": True},
        {"partitions_scored": ["TEST"]},
        {"status": "FRESH_HELDOUT_READY"},
    ],
)
def test_nonexposed_or_protected_manifest_refused_before_answers_read(tmp_path, change):
    manifest = {
        "schema_version": "course-parser-learning-evaluation-v2",
        "status": "DIAGNOSTIC_READY",
        "scope": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_NOT_FINAL_GENERALIZATION_ACCURACY",
        "protected_evaluation_opened": False,
        "partitions_scored": ["VALIDATION"],
        **change,
    }
    write_json(tmp_path / "evaluation_manifest.json", manifest)
    with pytest.raises(ValueError, match="only sealed, exposed"):
        load_exposed_evaluation(tmp_path)


# Detect changes to a saved evaluation before opening its source answers.
def test_tampered_evaluation_refused_before_input_answers_read(tmp_path):
    manifest = read_json(
        WORKSPACE
        / ("artifacts/validation-transfer-v1/release/original-evaluation/evaluation_manifest.json")
    )
    manifest["file_sha256"] = {"imported_course_truth.jsonl": "0" * 64}
    (tmp_path / "imported_course_truth.jsonl").write_text("invalid answer key", encoding="utf-8")
    write_json(tmp_path / "evaluation_manifest.json", manifest)
    with pytest.raises(ValueError, match="checksum changed"):
        load_exposed_evaluation(tmp_path)


# Reproduce the saved outcomes for the exposed fifteen-course subset and its overlapping
# completion criteria.
@pytest.mark.parametrize(
    "path,complete,joint,missing",
    [
        ("artifacts/progress-measurement-20261006/original_current", 0, 5, 4),
        ("artifacts/validation-transfer-v1/release/original-evaluation", 10, 11, 2),
    ],
)
def test_sealed_fifteen_course_outcomes_and_overlap(path, complete, joint, missing):
    rows, evidence = load_exposed_evaluation(WORKSPACE / path)
    diagnostic = evidence["summary"]["by_partition"]["VALIDATION"]
    assert len([x for x in rows if x["partition"] == "VALIDATION"]) == 15
    assert diagnostic["core"]["eligible"] == 15
    assert diagnostic["core"]["correct"] == complete
    assert diagnostic["confirmed_extent_and_description_value_exact"]["count"] == joint
    assert sum(diagnostic["core"]["not_distance_assessed"].values()) == missing
    if complete == 0:
        assert all(
            x["remaining_core_criteria"] == ["code"]
            for x in diagnostic["confirmed_extent_and_description_value_exact"]["courses"]
        )


# Check that scorer ambiguous course is not reported as absent header.
def test_scorer_ambiguous_course_is_not_reported_as_absent_header():
    rows, _ = load_exposed_evaluation(
        WORKSPACE / ("artifacts/validation-transfer-v1/release/original-evaluation")
    )
    ambiguous = [x for x in rows if x["pipeline_state"] == "ALIGNMENT_AMBIGUOUS"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["detection"]["status"] == "NOT_ASSESSED"
    assert ambiguous[0]["completion"]["core"]["failed_criteria_count"] is None


# Check that diagnostics are sealed and cannot overwrite existing results.
def test_diagnostics_are_sealed_and_cannot_overwrite_existing_results(tmp_path):
    evaluation = WORKSPACE / "artifacts/validation-transfer-v1/release/original-evaluation"
    before = sha256(evaluation / "evaluation_manifest.json")
    output = tmp_path / "diagnostics"
    manifest = write_diagnostics(evaluation, output)
    verify_files(output, manifest["file_sha256"])
    assert sha256(evaluation / "evaluation_manifest.json") == before
    assert manifest["parser_run"] is False
    assert manifest["fresh_holdout_opened"] is False
    with pytest.raises(FileExistsError):
        write_diagnostics(evaluation, output)
