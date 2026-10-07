# Tests for assembling course headings and fields using surrounding text and layout.
"""Whole-entry regressions, including deceptively course-shaped negatives."""

from __future__ import annotations

import importlib

import pytest

from course_parser.contextual import (
    ContextualContinuationResolver,
    ContextualCourseFieldParser,
    ContextualEntryClassifier,
    ContextualHeaderDetector,
    LineStyle,
)
from course_parser.learning_evaluation import complete_course_agrees, complete_course_eligible


# Check that distribution totals do not make listed courses independent.
def test_distribution_totals_do_not_make_listed_courses_independent(reference):
    records, _, decisions = extract(
        reference,
        [
            "CJC 101 Introduction to Criminal Justice",
            "CJC 355P Crime and Public Policy",
            "CJC 421 Criminal Behavior in the United States",
            "CJC 475P White Collar Crime",
            "12 hours social sciences, 9 hours junior-senior level.",
        ],
        heading="XX. Criminal Justice and Society",
    )
    assert not records
    assert any("SOURCE_DISTRIBUTION_REQUIREMENT_LIST" in d.reason_codes for d in decisions)


# Check that degree abbreviation inside requirement prose is a reference.
def test_degree_abbreviation_inside_requirement_prose_is_a_reference(reference):
    records, _, _ = extract(
        reference,
        [
            "B.A./B.S., Art 110P meets fine arts requirement.",
            "GEOL 101 Economic Geology",
            "Students examine deposits and describe the mineral systems they contain.",
            "GEOL 102 Resource Geology",
            "Students interpret geological resources and practice exploration methods.",
        ],
    )
    assert all(c.code.raw != "B" for c in records)


# Read the requested source text and geometry into the structures expected by the caller.
def extract(
    reference,
    lines,
    *,
    heading="Course Descriptions",
    total_pages=1,
    closing=True,
    geometry_by_index=None,
    return_stages=False,
):
    m = reference.models
    identity = m.SourceDocumentIdentity(
        source_document_id="synthetic",
        source_pdf_checksum="a" * 64,
        source_pdf_path="/synthetic-fixture.pdf",
    )
    nodes = []
    styles = {}
    for index, text in enumerate([heading, *lines, *(["DEGREE REQUIREMENTS"] if closing else [])]):
        nid = f"n{index}"
        geometry = m.BoundingBox(
            coordinate_system="PDF_POINTS_CANONICAL",
            x0=30,
            x1=450,
            y0=30 + index * 16,
            y1=40 + index * 16,
        )
        if geometry_by_index and index in geometry_by_index:
            geometry = m.BoundingBox(
                coordinate_system="PDF_POINTS_CANONICAL", **geometry_by_index[index]
            )
        anchor = m.EvidenceAnchor(
            source_document_id="synthetic",
            source_pdf_checksum="a" * 64,
            physical_page=1,
            node_id=nid,
            representation_id="synthetic-text",
            observed_text=text,
            extraction_method="PYMUPDF_EMBEDDED_TEXT",
            geometry=geometry,
        )
        nodes.append(
            m.GraphNode(
                node_id=nid,
                raw_text=text,
                evidence=(anchor,),
                engine="SYNTHETIC_FIXTURE",
                role=m.LayoutNodeRole.SECTION_HEADING
                if index in {0, len(lines) + 1}
                else m.LayoutNodeRole.LINE,
            )
        )
        styles[nid] = LineStyle(bold=bool(text.startswith("GEOL")), size=12)
    edges = []
    for a, b in zip(nodes, nodes[1:], strict=False):
        for kind in (m.EdgeKind.NEXT_IN_READING_ORDER, m.EdgeKind.SAME_COLUMN):
            edges.append(
                m.GraphEdge(
                    source_node_id=a.node_id,
                    target_node_id=b.node_id,
                    kind=kind,
                    status=m.ResolutionStatus.CONFIRMED,
                    reason="synthetic verified order",
                )
            )
    page = m.PageGraph(
        physical_page=1,
        nodes=tuple(nodes),
        edges=tuple(edges),
        status=m.ResolutionStatus.CONFIRMED,
        width=500,
        height=800,
        contributing_engines=("SYNTHETIC_FIXTURE",),
    )
    graph = m.DocumentGraph(
        identity=identity.model_copy(update={"total_physical_pages": total_pages}), pages=(page,)
    )
    profile = (
        importlib.import_module("catalog_mining.course_extraction_v3.document_grammar")
        .DeterministicDocumentGrammarProfiler()
        .profile(graph)
    )
    headers = ContextualHeaderDetector(m, styles).detect(graph, profile)
    decisions = ContextualEntryClassifier(m, styles).classify(graph, profile, headers)
    from course_parser.ownership import OwnedCourseBlockBuilder

    blocks = OwnedCourseBlockBuilder(m).build(graph, profile, headers, decisions)
    resolver = ContextualContinuationResolver(m)
    blocks = resolver.resolve(graph, profile, headers, decisions, blocks)
    fields = ContextualCourseFieldParser(m, styles)
    records = fields.parse(graph, profile, headers, blocks)
    if return_stages:
        return records, graph, headers, decisions, blocks, resolver, fields
    return records, headers, decisions


# Check that credits beside code before lower title are owned and hours kept separate.
def test_credits_beside_code_before_lower_title_are_owned_and_hours_kept_separate(reference):
    lines = [
        "GEOL 101",
        "3",
        "cr. 4",
        "hr.",
        "Mineral Systems",
        "Students examine deposits and interpret their formation.",
        "GEOL 102 Resource Geology",
        "Study of resources and their geological context.",
    ]
    geometry = {
        1: dict(x0=30, x1=110, y0=46, y1=58),
        2: dict(x0=300, x1=308, y0=49, y1=56),
        3: dict(x0=315, x1=345, y0=48, y1=56),
        4: dict(x0=350, x1=365, y0=49, y1=55),
        5: dict(x0=30, x1=190, y0=60, y1=70),
    }
    records, _, _ = extract(reference, lines, geometry_by_index=geometry)
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert first.raw_course_block == "\n".join(lines[:6])
    assert first.title.raw == "Mineral Systems"
    assert first.credits.raw.strip() == "3\ncr."
    assert "4" not in first.credits.raw
    assert first.description.raw == lines[5]


# Check that fragmented eligibility and administration do not become description.
def test_fragmented_eligibility_and_administration_do_not_become_description(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals. No previous",
            "knowledge of geology required.",
            "Lecture,",
            "demonstration, plus additional time.",
            "GEOL 102 Resource Geology",
            "Study of resources and their context.",
        ],
    )
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert first.description.raw.strip() == "Study of minerals."
    assert first.prerequisites.raw == "No previous\nknowledge of geology required."
    assert "Lecture,\ndemonstration" in first.raw_course_block


# Check that soft hyphen prerequisite label retains source but changes field ownership.
def test_soft_hyphen_prerequisite_label_retains_source_but_changes_field_ownership(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and deposits. Pre\u00ad",
            "requisite: GEOL 100.",
            "GEOL 102 Resource Geology",
            "Study of resources and their context.",
        ],
    )
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert first.description.raw.strip() == "Study of minerals and deposits."
    assert first.prerequisites.raw == "GEOL 100."
    assert "Pre\u00ad\nrequisite:" in first.raw_course_block


# Check that complete courses do not require explicit credits.
def test_complete_courses_do_not_require_explicit_credits(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and interpret measurements.",
        ],
    )
    assert len(records) == 2
    assert records[0].title.raw == "Mineral Systems"
    assert records[0].description.raw == "Study of minerals and their geological setting."
    assert str(records[0].boundary_status) == "CONFIRMED_BOUNDARY"
    assert records[0].credits.raw is None
    assert str(records[0].credits.status) == "NOT_RECOVERED"
    assert all("GEOL 102" not in x.description.raw for x in records)


# Check that repeated headers with prose inside requirement or reference container are
# rejected.
@pytest.mark.parametrize("heading", ["Degree Requirements", "Recommended Courses", "Index"])
def test_repeated_headers_with_prose_inside_requirement_or_reference_container_are_rejected(
    reference, heading
):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and interpret measurements.",
        ],
        heading=heading,
    )
    assert records == ()


# Check that inline historical description is preserved separately from title.
def test_inline_historical_description_is_preserved_separately_from_title(reference):
    records, _, _ = extract(
        reference,
        [
            "152 ROMAN EPIC Latin epic poetry and its historical development.",
            "Prerequisite: Latin 101.",
            "154 ROMAN SATIRE Study of literary forms and their influence.",
        ],
    )
    assert len(records) == 2
    first = next(x for x in records if x.code.raw == "152")
    assert first.title.raw == "ROMAN EPIC"
    assert first.description.raw == "Latin epic poetry and its historical development."
    assert "Prerequisite" not in first.description.raw
    # Every character of the owned source still appears in exactly one allocation.
    for index in {x.segment_index for x in first.field_allocations}:
        spans = sorted(
            (x for x in first.field_allocations if x.segment_index == index),
            key=lambda x: x.text_span.start,
        )
        assert all(
            a.text_span.end == b.text_span.start for a, b in zip(spans, spans[1:], strict=False)
        )


# Check that wrapped title does not consume first line of description.
def test_wrapped_title_does_not_consume_first_line_of_description(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Introduction to",
            "Mineral Exploration",
            "Students investigate ore deposits using field observations.",
            "GEOL 102 Resource Geology",
            "Interpretation of minerals and their geological setting.",
        ],
    )
    first = next(x for x in records if x.code.raw == "GEOL 101")
    assert first.title.raw == "Introduction to\nMineral Exploration"
    assert first.description.raw == "Students investigate ore deposits using field observations."


# Check that joint score requires correct description and confirmed boundary.
def test_joint_score_requires_correct_description_and_confirmed_boundary():
    # Build a source-observed field with the supplied exact text.
    def field(text):
        return {"raw": text, "status": "OBSERVED_EXACT"}

    gold = {
        "boundary_status": "CONFIRMED_BOUNDARY",
        "raw_course_block": "GEOL 101 Title\nA complete description.",
        "physical_pages": [1],
        "continuation_status": "NONE",
        "code": field("GEOL 101"),
        "title": field("Title"),
        "description": field("A complete description."),
        "credits": {"raw": None, "status": "ABSENT_IN_SOURCE"},
        "prerequisites": {"raw": None, "status": "ABSENT_IN_SOURCE"},
    }
    actual = {**gold, "description_status": "COMPLETE_DESCRIPTION"}
    assert complete_course_eligible(gold, all_fields=True)
    assert complete_course_agrees(actual, gold, all_fields=True)
    assert not complete_course_agrees({**actual, "boundary_status": "UNRESOLVED_BOUNDARY"}, gold)
    assert not complete_course_agrees({**actual, "description": field("A complete")}, gold)
    assert not complete_course_agrees(
        {**actual, "credits": {"raw": None, "status": "NOT_RECOVERED"}}, gold, all_fields=True
    )
    assert not complete_course_eligible(
        {**gold, "description": {"raw": None, "status": "NOT_RECOVERED"}}
    )


# Check that prerequisite course reference does not split the owned course.
def test_prerequisite_course_reference_does_not_split_the_owned_course(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "Prerequisite: completed prior work in",
            "GEOL 099 or instructor permission.",
            "GEOL 102 Exploration Methods",
            "Students investigate field measurements and interpret results.",
        ],
    )
    assert len(records) == 2
    first = next(x for x in records if x.code.raw == "GEOL 101")
    assert first.description.raw == "Study of minerals and their geological setting."
    assert "GEOL 099" in first.prerequisites.raw
    assert str(first.boundary_status) == "CONFIRMED_BOUNDARY"


# Check that selected window end is not a confirmed course boundary.
def test_selected_window_end_is_not_a_confirmed_course_boundary(reference):
    records, _, _ = extract(
        reference,
        ["GEOL 101 Mineral Systems", "Study of minerals and their geological setting."],
        total_pages=None,
        closing=False,
    )
    assert len(records) == 1
    assert str(records[0].boundary_status) == "UNRESOLVED_BOUNDARY"
    assert str(records[0].description.status) == "PARTIAL"


# Check that hours and offering continuations stay out of description.
def test_hours_and_offering_continuations_stay_out_of_description(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting. 2 hrs.",
            "lecture and occasional field trips.",
            "Offered",
            "Spring semester.",
            "GEOL 102 Exploration Methods",
            "Students investigate field measurements and interpret results.",
        ],
    )
    first = next(x for x in records if x.code.raw == "GEOL 101")
    assert first.description.raw.strip() == "Study of minerals and their geological setting."
    assert "field trips" in first.raw_course_block


# Check that credit metadata alone is not a course title.
def test_credit_metadata_alone_is_not_a_course_title(reference):
    records, _, _ = extract(
        reference,
        [
            "II (2 q.h.)",
            "An applied approach to urban planning and development.",
            "III (3 q.h.)",
            "Students investigate cities and their patterns of development.",
        ],
    )
    assert records == ()
