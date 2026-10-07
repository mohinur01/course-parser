# Tests for replaying known missed-course patterns from approved source fixtures.
"""Permitted source snapshots exercise audit questions, without supplying gold."""

from pathlib import Path

from regression_catalogue import fixture_cases

from course_parser.io import read_json, verify_files
from course_parser.omission_ranking import group_duplicate_flags
from course_parser.omission_signals import audit_page

ROOT = Path(__file__).parent / "data/omission_source_regressions_v1"


# Replay a permitted source snapshot through the omission audit without adding course answers.
def source_flags(name):
    assert (ROOT / (name + ".json")).resolve() in fixture_cases(ROOT.name)
    manifest = read_json(ROOT / "manifest.json")
    assert manifest["scope"] == "SOURCE_OBSERVATIONS_NOT_NEW_GOLD"
    verify_files(ROOT, manifest["file_sha256"])
    case = read_json(ROOT / (name + ".json"))
    assert case["provenance"]["source_truth_read"] is False
    assert case["provenance"]["protected_evaluation_opened"] is False
    return audit_page(case["geometry"], case["layout"], case["proposal"], case["binding"])


# Recover all seven review questions from decimal-number headings with separately printed
# title fragments.
def test_decimal_headers_with_separate_title_fragments_raise_seven_questions():
    flags = source_flags("decimal_code_only_headers")
    decimal = [f for f in flags if "HISTORICAL_IDENTIFIER" in f["signals"]]
    assert {f["raw_text"] for f in decimal} == {
        "2.862",
        "2.901",
        "2.902",
        "2.903",
        "2.904",
        "2.905",
        "2.906",
    }
    assert all("ADJACENT_SAME_ROW_TITLE" in f["corroborating_cues"] for f in decimal)
    assert all(f["course_truth_assigned"] is False for f in decimal)
    assert all(f["kind"] == "HEADER_WITHHELD_BY_ENTRY_STAGE" for f in decimal)


# Check that administrative flags stay available at low priority.
def test_administrative_flags_stay_available_at_low_priority():
    flags = source_flags("administrative_display_headings")
    rights = next(f for f in flags if f["raw_text"] == "RIGHTS AND PRIVACY ACT")
    assert rights["priority_tier"] == "LOW"
    assert "ADMINISTRATIVE_CONTEXT" in rights["corroborating_cues"]
    assert rights["course_truth_assigned"] is False


# Check that identical titles at distinct positions are not duplicates.
def test_identical_titles_at_distinct_positions_are_not_duplicates():
    flags = source_flags("same_title_distinct_course_positions")
    titles = [f for f in flags if f["raw_text"] == "Conservation of Natural Resources"]
    assert len(titles) == 2
    groups = group_duplicate_flags(titles)
    assert len(groups) == 2
    assert all(g["duplicate_count"] == 0 for g in groups)
    assert len({tuple(g["geometry"]) for g in groups}) == 2
    assert not any(f["raw_text"] == "144" for f in flags)


# Check that supported neighbor pattern preserves a course without printed description.
def test_supported_neighbor_pattern_preserves_a_course_without_printed_description():
    flags = source_flags("supported_pattern_without_description")
    practicum = next(f for f in flags if f["raw_text"] == "4360 Practice Management Practicum (3).")
    assert "MODEL_SUPPORTED_ENTRY_PATTERN" in practicum["corroborating_cues"]
    assert "FOLLOWING_DESCRIPTION_STRUCTURE" not in practicum["corroborating_cues"]
    assert practicum["course_truth_assigned"] is False
