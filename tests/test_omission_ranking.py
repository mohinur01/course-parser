# Tests for prioritizing possible omissions and grouping duplicates without approving courses.
"""Review priorities never confer course truth; grouping preserves provenance."""

import copy

import pytest

from course_parser.omission_ranking import group_duplicate_flags, score_flag


# Build a synthetic omission flag with the source cues needed for ranking.
def flag(identifier="a", *, box=(10, 10, 100, 20), text="Economic Geology", **kwargs):
    return {
        "flag_id": identifier,
        "source_pdf_checksum": "pdf-a",
        "physical_page": 10,
        "geometry_frame": "PDF_POINTS_CANONICAL",
        "geometry": list(box),
        "raw_text": text,
        "signals": ["HISTORICAL_IDENTIFIER"],
        "corroborating_cues": ["FOLLOWING_DESCRIPTION_STRUCTURE"],
        "course_truth_assigned": False,
        **kwargs,
    }


# Check that score is explainable provisional and does not mutate input.
def test_score_is_explainable_provisional_and_does_not_mutate_input():
    source = flag(
        corroborating_cues=[
            "MODEL_SUPPORTED_ENTRY_PATTERN",
            "FOLLOWING_DESCRIPTION_STRUCTURE",
            "INLINE_TITLE_PROSE_TRANSITION",
            "EXPLICIT_PRINTED_CREDIT_VALUE",
            "COURSE_SECTION_CONTEXT",
            "DISTINCTIVE_TYPOGRAPHY",
            "MATCHING_ENTRY_TYPOGRAPHY",
        ]
    )
    before = copy.deepcopy(source)
    scored = score_flag(source)
    assert scored["omission_score"] == 15  # Do not double count typography or prose.
    assert scored["priority_tier"] == "HIGH"
    assert sum(r["points"] for r in scored["priority_reasons"]) == 15
    assert scored["score_is_probability"] is False
    assert scored["score_uses_review_answers"] is False
    assert scored["course_truth_assigned"] is False
    assert source == before


# Do not strengthen an omission flag using weak repetition or prerequisite references.
@pytest.mark.parametrize(
    "cues",
    [
        [],
        ["REPEATED_ENTRY_PATTERN"],
        ["NEARBY_CREDIT_OR_PREREQUISITE"],
        ["REPEATED_ENTRY_PATTERN", "NEARBY_CREDIT_OR_PREREQUISITE"],
    ],
)
def test_weak_repetition_and_prerequisite_mentions_earn_no_extra_points(cues):
    assert score_flag(flag(corroborating_cues=cues))["omission_score"] == 4


# Check which review category each boundary score receives.
@pytest.mark.parametrize("score,tier", [(4, "LOW"), (5, "MEDIUM"), (7, "MEDIUM"), (8, "HIGH")])
def test_priority_thresholds(score, tier):
    cues = {
        4: [],
        5: ["DISTINCTIVE_TYPOGRAPHY"],
        7: ["FOLLOWING_DESCRIPTION_STRUCTURE"],
        8: ["FOLLOWING_DESCRIPTION_STRUCTURE", "DISTINCTIVE_TYPOGRAPHY"],
    }[score]
    result = score_flag(flag(corroborating_cues=cues))
    assert (result["omission_score"], result["priority_tier"]) == (score, tier)


# Lower the priority of a flag in negative context while keeping the original flag for review.
def test_negative_context_lowers_priority_without_discarding_flag():
    result = score_flag(
        flag(
            corroborating_cues=[
                "FOLLOWING_DESCRIPTION_STRUCTURE",
                "REQUIREMENT_LIST_CONTEXT",
                "PARSER_REFERENCE_OR_NONCOURSE",
                "ADMINISTRATIVE_CONTEXT",
            ]
        )
    )
    assert result["omission_score"] == -1  # Requirement and parser cues counted once.
    assert result["priority_tier"] == "LOW"
    assert result["course_truth_assigned"] is False


# Group overlapping duplicate flags without losing observations or transferring evidence
# between them.
def test_overlapping_aliases_group_losslessly_without_borrowing_cues():
    a = score_flag(flag("a", corroborating_cues=["FOLLOWING_DESCRIPTION_STRUCTURE"]))
    b = score_flag(
        flag(
            "b",
            box=(11, 10, 101, 20),
            text=" ECONOMIC  Geology\n",
            corroborating_cues=["EXPLICIT_PRINTED_CREDIT_VALUE", "DISTINCTIVE_TYPOGRAPHY"],
        )
    )
    original = copy.deepcopy([a, b])
    groups = group_duplicate_flags([a, b])
    assert len(groups) == 1
    group = groups[0]
    assert group["member_flag_ids"] == ["a", "b"]
    assert group["member_flags"] == original
    assert group["duplicate_count"] == 1
    assert group["omission_score"] == 7  # No synthetic union that would score 10.
    assert [a, b] == original
    assert groups == group_duplicate_flags([b, a])


# Check that distinct printed entries documents frames and text are not merged.
@pytest.mark.parametrize(
    "kwargs",
    [
        {"box": (10, 40, 100, 50)},
        {"source_pdf_checksum": "pdf-b"},
        {"physical_page": 11},
        {"geometry_frame": "CROP_PIXELS"},
        {"text": "Economic Geo1ogy"},
        {"text": "Economic-Geology"},
    ],
)
def test_distinct_printed_entries_documents_frames_and_text_are_not_merged(kwargs):
    assert len(group_duplicate_flags([score_flag(flag("a")), score_flag(flag("b", **kwargs))])) == 2


# Check that complete link cannot bridge distinct locations.
def test_complete_link_cannot_bridge_distinct_locations():
    flags = [
        score_flag(flag(i, box=(x, 10, x + 100, 20))) for i, x in [("a", 0), ("b", 10), ("c", 20)]
    ]
    groups = group_duplicate_flags(flags)
    assert len(groups) == 2
    assert sorted(i for g in groups for i in g["member_flag_ids"]) == ["a", "b", "c"]


# Reject invalid geometry and duplicate identifiers instead of silently grouping them.
def test_invalid_boxes_and_duplicate_ids_fail_explicitly():
    with pytest.raises(ValueError, match="finite"):
        group_duplicate_flags([score_flag(flag(box=(10, float("nan"), 100, 20)))])
    with pytest.raises(ValueError, match="IDs"):
        group_duplicate_flags([score_flag(flag()), score_flag(flag())])
