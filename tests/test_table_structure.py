# Tests for separating course inventories from requirement grids while preserving their
# original text.
"""Whole table entries and lossless alternative layout interpretations."""

from __future__ import annotations

import importlib

import pytest

from course_parser.contextual import ContextualEntryClassifier, ContextualHeaderDetector, LineStyle
from course_parser.learning_layout import prose_column_groups, repair_parallel_prose
from course_parser.ownership import OwnedCourseBlockBuilder
from course_parser.ownership_metrics import ownership_rates
from course_parser.table_structure import layout_conservation, table_contexts


# Build a synthetic table layout with separate cell locations and course ownership evidence.
def table_layout(reference, heading="Course Descriptions", narrative=True):
    m = reference.models
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    regions = []
    texts = [heading]
    for i in range(2):
        texts.extend(
            [
                f"GEOL {101 + i}",
                "Economic Geology",
                "3",
                "Students examine geological deposits and interpret their formation "
                "through field evidence."
                if narrative
                else "Choose one course from this list of approved program electives.",
            ]
        )
    for index, text in enumerate(texts):
        row, col = divmod(index - 1, 4)
        x0 = [20, 100, 245, 280][col] if index else 20
        y0 = 60 + row * 80 if index else 20
        b = m.BoundingBox(
            coordinate_system="PDF_POINTS_CANONICAL",
            x0=x0,
            x1=x0 + (300 if col == 3 else 70),
            y0=y0,
            y1=y0 + 20,
        )
        anchor = m.EvidenceAnchor(
            source_document_id="fixture",
            source_pdf_checksum="a" * 64,
            physical_page=1,
            observed_text=text,
            representation_id="native",
            node_id=f"n{index}",
            extraction_method="PYMUPDF_EMBEDDED_TEXT",
            geometry=b,
        )
        regions.append(
            module.LayoutRegionProposal(
                region_id=f"n{index}",
                raw_text=text,
                role="TABLE_CELL" if index else "SECTION_HEADING",
                geometry=b,
                evidence=(anchor,),
                source_order=index,
                column_id="c0",
                table_id="t0" if index else None,
                row_id=f"r{row}" if index else None,
                cell_id=f"cell{index}" if index else None,
            )
        )
    return module.PageLayoutProposal(
        dataset_page_id="test:p1",
        source_document_id="fixture",
        source_pdf_checksum="a" * 64,
        physical_page=1,
        engine="PYMUPDF_GEOMETRY",
        engine_version="test",
        status="COMPLETE",
        page_width=600,
        page_height=800,
        regions=tuple(regions),
        reading_order=tuple(
            module.ReadingOrderProposal(
                source_region_id=a.region_id,
                target_region_id=b.region_id,
                status="CONFIRMED",
                reason="fixture rows",
            )
            for a, b in zip(regions, regions[1:], strict=False)
        ),
    )


# Decide whether each possible heading starts an independent course or belongs to a reference,
# requirements list or unresolved case.
def classify(reference, layout):
    m = reference.models
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    graph = m.DocumentGraph(
        identity=m.SourceDocumentIdentity(
            source_document_id="fixture",
            source_pdf_checksum="a" * 64,
            source_pdf_path="/fixture.pdf",
        ),
        pages=(module.proposal_to_page_graph(layout),),
    )
    profile = (
        importlib.import_module("catalog_mining.course_extraction_v3.document_grammar")
        .DeterministicDocumentGrammarProfiler()
        .profile(graph)
    )
    styles = {r.region_id: LineStyle(bold=r.raw_text.startswith("GEOL")) for r in layout.regions}
    candidates = ContextualHeaderDetector(m, styles).detect(graph, profile)
    decisions = ContextualEntryClassifier(
        m, styles, table_contexts=table_contexts((layout,))
    ).classify(graph, profile, candidates)
    return graph, profile, candidates, decisions


# Keep a course-inventory row tied to its complete original text cells.
def test_inventory_rows_own_only_their_complete_source_cells(reference):
    layout = table_layout(reference)
    graph, profile, candidates, decisions = classify(reference, layout)
    assert len(decisions) == 2
    assert all(d.status == "INDEPENDENT_COURSE" for d in decisions)
    assert all(c.arrangement == "TABLE_ROW" for c in candidates)
    blocks = OwnedCourseBlockBuilder(reference.models).build(graph, profile, candidates, decisions)
    assert len(blocks) == 2
    assert {frozenset(b.node_ids) for b in blocks} == {
        frozenset(f"n{i}" for i in range(1, 5)),
        frozenset(f"n{i}" for i in range(5, 9)),
    }


# Recognize a requirements table even when its rows contain course codes, credits and
# descriptive text.
def test_requirements_win_even_with_repeated_codes_credits_and_narrative(reference):
    layout = table_layout(reference, "Degree Requirements", narrative=False)
    graph, profile, candidates, decisions = classify(reference, layout)
    assert len(decisions) == 2
    assert all(d.status == "COURSE_REFERENCE" for d in decisions)
    assert not OwnedCourseBlockBuilder(reference.models).build(
        graph, profile, candidates, decisions
    )


# Check that grid without inventory container is unresolved.
def test_grid_without_inventory_container_is_unresolved(reference):
    _, _, _, decisions = classify(reference, table_layout(reference, "Geological Studies"))
    assert all(d.status == "UNRESOLVED" for d in decisions)


# Keep course-inventory rows correctly assigned when their printed credits are absent.
def test_inventory_without_printed_credits_still_owns_its_rows(reference):
    layout = table_layout(reference)
    raw = layout.model_dump(mode="json")
    raw["regions"] = [r for r in raw["regions"] if r["region_id"] not in {"n3", "n7"}]
    raw["reading_order"] = [
        {
            "source_region_id": a["region_id"],
            "target_region_id": b["region_id"],
            "status": "CONFIRMED",
            "reason": "complete creditless fixture rows",
        }
        for a, b in zip(raw["regions"], raw["regions"][1:], strict=False)
    ]
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    graph, profile, candidates, decisions = classify(
        reference, module.PageLayoutProposal.model_validate(raw)
    )
    assert all(d.status == "INDEPENDENT_COURSE" for d in decisions)
    blocks = OwnedCourseBlockBuilder(reference.models).build(graph, profile, candidates, decisions)
    assert len(blocks) == 2


# Check that conservation detects changed text and preserves row ids.
def test_conservation_detects_changed_text_and_preserves_row_ids(reference):
    layout = table_layout(reference)
    audit = layout_conservation(layout, layout)
    assert audit["source_cells"] == 9 and not audit["interpretation_changes"]
    bad = layout.model_copy(
        update={
            "regions": (
                layout.regions[0].model_copy(update={"raw_text": "altered"}),
                *layout.regions[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="source cell evidence"):
        layout_conservation(layout, bad)


# Check that long description table is not flattened as prose.
def test_long_description_table_is_not_flattened_as_prose(reference):
    layout = table_layout(reference)
    raw = layout.model_dump(mode="json")
    regions = []
    for col in range(2):
        for row in range(20):
            regions.append(
                {
                    **raw["regions"][1],
                    "region_id": f"x{col}-{row}",
                    "row_id": f"r{row}",
                    "cell_id": f"cell{col}-{row}",
                    "raw_text": ["Title", "Description"][col]
                    if row == 0
                    else "A long narrative cell that belongs to this particular table row.",
                    "geometry": {
                        **raw["regions"][1]["geometry"],
                        "x0": 20 + col * 300,
                        "x1": 280 + col * 300,
                        "y0": 50 + row * 20,
                        "y1": 65 + row * 20,
                    },
                }
            )
    assert prose_column_groups(regions, 600, 800, fragmented=True) == []
    raw["regions"], raw["reading_order"] = regions, []
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    proposal = module.PageLayoutProposal.model_validate(raw)
    assert repair_parallel_prose(proposal, module, reference.models, fragmented=True) == proposal


# Check that ownership recall keeps missing courses and failed pages.
def test_ownership_recall_keeps_missing_courses_and_failed_pages():
    counts = dict(
        complete_content_correct=2,
        central_course_proposals=4,
        confirmed_extent_exact_matched=3,
        source_boundary_eligible=6,
        description_exact_matched=2,
        source_description_eligible=6,
        zero_course_pages=3,
        false_proposals_on_zero_course_pages=1,
        clean_zero_course_pages=2,
    )
    result = ownership_rates(counts)
    assert result["complete_course_precision_conservative"] == 0.5
    assert result["owned_extent_recall"] == 0.5
    assert result["description_missing_or_incorrect"] == 4
    assert ownership_rates({k: 0 for k in counts})["owned_extent_recall"] is None
