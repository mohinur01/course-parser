# Tests for finding possible missed headings without adding them to extracted course records.
"""Recall paths independent of modern-code proposals; source questions, not truth."""

import copy

import pytest

from course_parser.omission_review import select_crop_flags
from course_parser.omission_signals import audit_page


# Build a small test input with the source identity and values required by this test group.
def page(texts, *, bold=(), proposal=None):
    lines = [
        {
            "text": text,
            "line_id": f"b{i}_l0",
            "block_index": i,
            "line_index": 0,
            "bbox_canonical_pdf_points": [30, 30 + i * 24, 450, 42 + i * 24],
            "spans": [{"text": text, "flags": 16 if i in bold else 0, "size": 10}],
        }
        for i, text in enumerate(texts)
    ]
    geometry = {
        "physical_page": 2,
        "canonical_width_points": 500,
        "canonical_height_points": 700,
        "lines": lines,
    }
    layout = {
        "physical_page": 2,
        "dataset_page_id": "sample:p2",
        "page_width": 500,
        "page_height": 700,
        "status": "COMPLETE",
    }
    binding = {
        "dataset_page_id": "sample",
        "source_pdf_checksum": "synthetic",
        "learning_partition": "TUNING",
    }
    return geometry, layout, proposal or {"blocks": [], "candidates": [], "decisions": []}, binding


# Raise review questions for older numeric headings that lack a modern subject prefix.
@pytest.mark.parametrize(
    "header", ["17. Mineralogy", "101-102 Economic Geology", "IV. Petrology", "A. Mineral Systems"]
)
def test_historical_identifiers_without_a_modern_subject_code(header):
    inputs = page(
        [
            "Courses of Instruction",
            header,
            "Students investigate mineral systems and their geological history.",
        ]
    )
    snapshot = copy.deepcopy(inputs)
    flags = audit_page(*inputs)
    flag = next(f for f in flags if f["raw_text"] == header)
    assert "HISTORICAL_IDENTIFIER" in flag["signals"]
    assert "FOLLOWING_DESCRIPTION_STRUCTURE" in flag["corroborating_cues"]
    assert flag["course_truth_assigned"] is False
    assert flag["review_status"] == "PENDING_SOURCE_REVIEW"
    assert inputs == snapshot


# Check that title only creditless course is flagged from typography and prose.
def test_title_only_creditless_course_is_flagged_from_typography_and_prose():
    inputs = page(
        [
            "Courses of Instruction",
            "Field Methods",
            "Students investigate mapping techniques and field measurements in practice.",
        ],
        bold=(1,),
    )
    flag = next(f for f in audit_page(*inputs) if f["raw_text"] == "Field Methods")
    assert "HEADER_WITHOUT_SUBJECT_CODE" in flag["signals"]
    assert "DISTINCTIVE_TYPOGRAPHY" in flag["corroborating_cues"]
    assert "NEARBY_CREDIT_OR_PREREQUISITE" not in flag["corroborating_cues"]


# Check that requirement list is not promoted by repeated numbered entries.
def test_requirement_list_is_not_promoted_by_repeated_numbered_entries():
    flags = audit_page(
        *page(["Degree Requirements", "17. Mineralogy", "18. Petrology", "19. Field Methods"])
    )
    assert all(f["review_priority"] == "CONTEXT_CONFLICT" for f in flags)
    assert all(f["course_truth_assigned"] is False for f in flags)


# Check that typography alone does not flag faculty or page furniture.
def test_typography_alone_does_not_flag_faculty_or_page_furniture():
    flags = audit_page(
        *page(
            ["Colorado School of Mines 109", "Director of Alumni Affairs", "3 credits"],
            bold=(0, 1, 2),
        )
    )
    assert flags == []


# Recognize possible historical headings even when title and description share a line without
# bold text.
def test_historical_inline_title_description_without_font_emphasis():
    flags = audit_page(
        *page(
            [
                "203 REPUBLICAN PROSE Reading in Caesar and Sallust, "
                "and in the speeches of Cicero.",
                "204 EPIC POETS Reading in Lucretius, Vergil, Ovid, and others. Prerequisite: 101,",
                "251 ROMAN LETTERS Selected letters of Cicero and Pliny are studied closely.",
                "204 or concurrent enrollment. Three hours. Mr. Pooley.",
            ]
        )
    )
    epic = next(flag for flag in flags if flag["raw_text"].startswith("204 EPIC"))
    assert "HISTORICAL_IDENTIFIER" in epic["signals"]
    assert "INLINE_TITLE_PROSE_TRANSITION" in epic["corroborating_cues"]
    assert not any(flag["raw_text"].startswith("204 or") for flag in flags)


# Check that codefree inline title description remains a review question.
def test_codefree_inline_title_description_remains_a_review_question():
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "EPIC POETS Reading in ancient poetry with close textual analysis.",
                "GEOL 101 Mineralogy",
                "GEOL 102 Petrology",
            ]
        )
    )
    flag = next(flag for flag in flags if flag["raw_text"].startswith("EPIC POETS"))
    assert "HEADER_WITHOUT_SUBJECT_CODE" in flag["signals"]
    assert flag["course_truth_assigned"] is False


# Check that credit line is not a historical header.
def test_credit_line_is_not_a_historical_header():
    flags = audit_page(*page(["Courses of Instruction", "3 credits", "4 semester hours"]))
    assert not any("HISTORICAL_IDENTIFIER" in f["signals"] for f in flags)


# Check that bold credit contact hour and grading labels are not course headers.
def test_bold_credit_contact_hour_and_grading_labels_are_not_course_headers():
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "3 credit hours",
                "Lab 3 Hrs",
                "Lec 4 Hrs",
                "Pass/No Pass Only",
                "Students investigate minerals and the geological history of the earth.",
            ],
            bold=(1, 2, 3, 4),
        )
    )
    assert not any(
        flag["raw_text"] in {"3 credit hours", "Lab 3 Hrs", "Lec 4 Hrs", "Pass/No Pass Only"}
        for flag in flags
    )


# Check that swallowed title and withheld header are different review reasons.
def test_swallowed_title_and_withheld_header_are_different_review_reasons():
    proposal = {
        "candidates": [
            {"candidate_id": "first", "node_ids": ["sample:p2:p2:b1_l0"]},
            {"candidate_id": "third", "node_ids": ["sample:p2:p2:b5_l0"]},
        ],
        "decisions": [{"candidate_id": "third", "status": "AMBIGUOUS"}],
        "blocks": [
            {
                "candidate_id": "first",
                "block_id": "owned",
                "node_ids": ["sample:p2:p2:b1_l0", "sample:p2:p2:b2_l0", "sample:p2:p2:b3_l0"],
            }
        ],
    }
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "GEOL 101 Mineralogy",
                "Study of minerals and their chemical and physical properties.",
                "Economic Geology",
                "Students investigate deposits and extraction methods in the field.",
                "GEOL 103 Field Methods",
            ],
            bold=(3,),
            proposal=proposal,
        )
    )
    assert not any(f["raw_text"] == "GEOL 101 Mineralogy" for f in flags)
    swallowed = next(f for f in flags if f["raw_text"] == "Economic Geology")
    assert swallowed["kind"] == "POSSIBLE_HEADER_INSIDE_OWNED_BODY"
    assert swallowed["competing_block_ids"] == ["owned"]
    withheld = next(f for f in flags if f["raw_text"] == "GEOL 103 Field Methods")
    assert withheld["kind"] == "HEADER_WITHHELD_BY_ENTRY_STAGE"
    assert withheld["existing_candidate_decisions"][0]["status"] == "AMBIGUOUS"


# Check that uncovered narrative is found with no header lexeme.
def test_uncovered_narrative_is_found_with_no_header_lexeme():
    inputs = page(
        [
            "Courses of Instruction",
            "The geological history is studied through extensive laboratory work.",
            "Measurements are compared with geological maps and field observations.",
            "Practical investigations introduce a range of quantitative methods.",
        ]
    )
    for i in (1, 2, 3):
        inputs[0]["lines"][i].update(block_index=1, line_index=i - 1)
    flags = audit_page(*inputs)
    assert sum("UNCOVERED_DESCRIPTION_BLOCK" in f["signals"] for f in flags) == 1


# Reject mismatched coordinate systems and leave missing page dimensions explicitly
# unresolved.
def test_source_frame_mismatch_rejected_and_unresolved_dimensions_explicit():
    inputs = page(["Courses of Instruction", "17. Mineralogy"])
    inputs[1]["page_width"] = 700
    with pytest.raises(ValueError, match="coordinate frame"):
        audit_page(*inputs)
    inputs[1].update(page_width=None, page_height=None, status="PARTIAL")
    flags = audit_page(*inputs)
    assert "SOURCE_ORDER_UNRESOLVED" in flags[0]["corroborating_cues"]


# Check that crop selection is source only balanced and tuning only.
def test_crop_selection_is_source_only_balanced_and_tuning_only():
    flags = audit_page(
        *page(
            ["Courses of Instruction", "17. Mineralogy", "Field Methods", "GEOL 103 Petrology"],
            bold=(2,),
        )
    )
    validation = copy.deepcopy(flags)
    for flag in validation:
        flag.update(learning_partition="VALIDATION", flag_id="validation-" + flag["flag_id"])
    selected = select_crop_flags(flags + validation, 3)
    assert len(selected) == 3
    assert all(f["learning_partition"] == "TUNING" for f in selected)
    assert {s for f in selected for s in f["signals"]} >= {
        "HISTORICAL_IDENTIFIER",
        "HEADER_WITHOUT_SUBJECT_CODE",
        "MODERN_IDENTIFIER",
    }


# Check that decimal long numeric and code only headers remain review questions.
@pytest.mark.parametrize(
    "header", ["2.862 Systems Engineering", "4360 PRACTICE MANAGEMENT", "591, 592 Special Projects"]
)
def test_decimal_long_numeric_and_code_only_headers_remain_review_questions(header):
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                header,
                "Students investigate the subject through laboratory work and field observations.",
            ]
        )
    )
    flag = next(f for f in flags if f["raw_text"] == header)
    assert "HISTORICAL_IDENTIFIER" in flag["signals"]
    assert flag["course_truth_assigned"] is False


# Award credit-value evidence only when a numeric credit amount is actually printed.
@pytest.mark.parametrize("metadata", ["Prerequisite: GEOL 201", "(2-4)", "Three hours."])
def test_only_explicit_numeric_credit_values_earn_credit_points(metadata):
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "535 Environmental Economics",
                metadata,
                "Students investigate environmental questions using economic theories and methods.",
            ]
        )
    )
    flag = next(f for f in flags if f["raw_text"].startswith("535"))
    assert "EXPLICIT_PRINTED_CREDIT_VALUE" not in flag["corroborating_cues"]


# Stop collecting description and credit evidence when the next possible heading begins.
def test_prose_and_credit_points_stop_before_next_header():
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "535 Environmental Economics",
                "542 Conservation Biology (3 credits)",
                "Students investigate species conservation and modern biological research.",
            ]
        )
    )
    flag = next(f for f in flags if f["raw_text"].startswith("535"))
    assert "FOLLOWING_DESCRIPTION_STRUCTURE" not in flag["corroborating_cues"]
    assert "EXPLICIT_PRINTED_CREDIT_VALUE" not in flag["corroborating_cues"]


# Check that lowercase body mention is not requirement heading.
def test_lowercase_body_mention_is_not_requirement_heading():
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "The major themes in mineral systems include requirements for field research.",
                "535 Environmental Economics",
                "Students investigate environmental questions using economic theories and methods.",
            ]
        )
    )
    flag = next(f for f in flags if f["raw_text"].startswith("535"))
    assert "REQUIREMENT_LIST_CONTEXT" not in flag["corroborating_cues"]
    assert "COURSE_SECTION_CONTEXT" in flag["corroborating_cues"]


# Check that supported peers supply pattern points but repeated unresolved codes do not.
def test_supported_peers_supply_pattern_points_but_repeated_unresolved_codes_do_not():
    inputs = page(
        [
            "Courses of Instruction",
            "535 Environmental Economics",
            "Students investigate environmental questions using economic theories and methods.",
            "542 Conservation Biology",
            "543 Economic Structure",
        ]
    )
    proposal = inputs[2]
    for i in (3, 4):
        cid = str(i)
        proposal["candidates"].append(
            {"candidate_id": cid, "status": "SUPPORTED", "node_ids": [f"sample:p2:p2:b{i}_l0"]}
        )
        proposal["decisions"].append({"candidate_id": cid, "status": "INDEPENDENT_COURSE"})
    flag = next(f for f in audit_page(*inputs) if f["raw_text"].startswith("535"))
    assert "MODEL_SUPPORTED_ENTRY_PATTERN" in flag["corroborating_cues"]
    assert len(flag["model_supported_peer_nodes"]) == 2
    for decision in proposal["decisions"]:
        decision["status"] = "AMBIGUOUS"
    ambiguous = next(f for f in audit_page(*inputs) if f["raw_text"].startswith("535"))
    assert "MODEL_SUPPORTED_ENTRY_PATTERN" not in ambiguous["corroborating_cues"]


# Check that relative geometry keeps priority under uniform scaling.
def test_relative_geometry_keeps_priority_under_uniform_scaling():
    inputs = page(
        [
            "Courses of Instruction",
            "535 Environmental Economics",
            "Students investigate environmental questions using economic theories and methods.",
        ]
    )
    initial = next(f for f in audit_page(*inputs) if f["raw_text"].startswith("535"))
    inputs[0]["canonical_width_points"] *= 3
    inputs[0]["canonical_height_points"] *= 3
    inputs[1]["page_width"] *= 3
    inputs[1]["page_height"] *= 3
    for line in inputs[0]["lines"]:
        line["bbox_canonical_pdf_points"] = [v * 3 for v in line["bbox_canonical_pdf_points"]]
        for span in line["spans"]:
            span["size"] *= 3
    scaled = next(f for f in audit_page(*inputs) if f["raw_text"].startswith("535"))
    assert (initial["signals"], initial["corroborating_cues"], initial["omission_score"]) == (
        scaled["signals"],
        scaled["corroborating_cues"],
        scaled["omission_score"],
    )


# Check that isolated number without a nearby same row title is not a header.
def test_isolated_number_without_a_nearby_same_row_title_is_not_a_header():
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "4",
                "Students investigate the subject with laboratory and field observations.",
            ]
        )
    )
    assert not any(f["raw_text"] == "4" for f in flags)


# Check that administrative word in course title does not veto a question.
def test_administrative_word_in_course_title_does_not_veto_a_question():
    flags = audit_page(
        *page(
            [
                "Courses of Instruction",
                "GEOL 535 Administrative Geology",
                "Students investigate field management and administrative procedures for mapping.",
            ]
        )
    )
    header = next(f for f in flags if f["raw_text"].startswith("GEOL 535"))
    assert "ADMINISTRATIVE_CONTEXT" not in header["corroborating_cues"]
    assert header["course_truth_assigned"] is False
