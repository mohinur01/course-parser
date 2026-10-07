# Tests for preserving course text when layout tools disagree about reading order.
"""Regression checks for source ordering and unique course ownership."""

import importlib
from types import SimpleNamespace

import pytest

from course_parser.engine_order import project_region_order
from course_parser.layout_models import Box, PagePredictions, Region
from course_parser.learning_layout import baseline_rows
from course_parser.ownership import audit_owned_blocks


# Build a synthetic native layout that preserves the original original text pieces.
def native_proposal(reference):
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    m = reference.models
    regions = []
    for index, text in enumerate(["CLASSICS", "101 INTRODUCTION", "Printed description."]):
        geometry = m.BoundingBox(
            coordinate_system="PDF_POINTS_CANONICAL",
            x0=30,
            x1=230,
            y0=30 + index * 20,
            y1=40 + index * 20,
        )
        anchor = m.EvidenceAnchor(
            source_document_id="synthetic",
            source_pdf_checksum="a" * 64,
            physical_page=1,
            node_id=f"n{index}",
            representation_id="source",
            observed_text=text,
            extraction_method="PYMUPDF_EMBEDDED_TEXT",
            geometry=geometry,
        )
        regions.append(
            module.LayoutRegionProposal(
                region_id=f"n{index}",
                raw_text=text,
                geometry=geometry,
                evidence=(anchor,),
                role="SECTION_HEADING" if index == 0 else "LINE",
                source_order=index,
                column_id="c0",
            )
        )
    return module, module.PageLayoutProposal(
        dataset_page_id="synthetic",
        source_document_id="synthetic",
        source_pdf_checksum="a" * 64,
        physical_page=1,
        engine="PYMUPDF_GEOMETRY",
        engine_version="fixture",
        status="COMPLETE",
        page_width=500,
        page_height=800,
        regions=tuple(regions),
    )


# Build one external layout region in its declared coordinate frame for a test.
def model_region(identifier, y0, *, furniture=False):
    return Region(
        region_id=identifier,
        box=Box(x0=29, x1=231, y0=y0, y1=y0 + 12),
        predicted_text="MODEL TEXT MUST NOT REPLACE SOURCE",
        origin="DOCLING",
        label="page_header" if furniture else "text",
        source_ref=identifier,
        furniture=furniture,
    )


# Check that external furniture label does not delete a native department heading.
def test_external_furniture_label_does_not_delete_a_native_department_heading(reference):
    module, native = native_proposal(reference)
    prediction = PagePredictions(
        engine="DOCLING",
        engine_version="synthetic",
        regions=(
            model_region("r0", 29, furniture=True),
            model_region("r1", 49),
            model_region("r2", 69),
        ),
        reading_order=("r1", "r2"),
        reading_order_status="MODEL_PROPOSED",
        assignments=(),
    )
    result = project_region_order(native, prediction, module)
    assert result.regions == native.regions
    assert [(e.source_region_id, e.target_region_id) for e in result.reading_order] == [
        ("n0", "n1"),
        ("n1", "n2"),
    ]
    assert result.status == "COMPLETE"
    assert any(w.endswith("LABEL_DISAGREEMENTS:1") for w in result.warnings)


# Keep text that an external layout tool cannot place, marking its location unresolved.
def test_external_orphan_or_ambiguous_cells_survive_as_unresolved_evidence(reference):
    module, native = native_proposal(reference)
    prediction = PagePredictions(
        engine="DOCLING",
        engine_version="synthetic",
        regions=(model_region("r0", 29), model_region("r1", 49), model_region("r1_duplicate", 49)),
        reading_order=("r0", "r1", "r1_duplicate"),
        reading_order_status="MODEL_PROPOSED",
        assignments=(),
    )
    result = project_region_order(native, prediction, module)
    assert result.regions == native.regions
    assert result.status == "PARTIAL"
    assert all(e.status == "UNRESOLVED" for e in result.reading_order)
    assert any(w.endswith("NATIVE_CELLS:2") for w in result.warnings)


# Build a synthetic source region with the geometry required by this test.
def region(text, x0, x1, y0, y1, order):
    return dict(raw_text=text, source_order=order, geometry=dict(x0=x0, x1=x1, y0=y0, y1=y1))


# Read a mixed-height OCR row in its printed order: code, credit fragments, then title.
def test_mixed_height_ocr_row_reads_code_then_credit_fragments_then_title():
    cells = [
        region("3", 138, 142, 25.1, 32.3, 1),
        region("cr. 3", 145, 160, 24.9, 32.1, 2),
        region("hr.", 164, 172, 25.9, 31.6, 3),
        region("BIOL 1810", 29, 67, 23.3, 33.3, 0),
        region("Science in Elementary Education", 29, 141, 33.4, 41.7, 4),
    ]
    assert [c["raw_text"] for c in baseline_rows(cells)] == [
        "BIOL 1810",
        "3",
        "cr. 3",
        "hr.",
        "Science in Elementary Education",
    ]
    assert sorted(cells, key=lambda c: c["source_order"]) == sorted(
        baseline_rows(cells), key=lambda c: c["source_order"]
    )


# Check that vertical overlap does not merge two lines at same horizontal location.
def test_vertical_overlap_does_not_merge_two_lines_at_same_horizontal_location():
    a = region("A title", 30, 180, 33, 43, 0)
    b = region("A", 30, 35, 40, 52, 1)
    c = region("description continues", 40, 190, 44, 52, 2)
    assert baseline_rows([c, b, a]) == [a, b, c]


# Check that two courses cannot own one source node.
def test_two_courses_cannot_own_one_source_node():
    n = SimpleNamespace(node_id="source-line", raw_text="printed source", evidence=("anchor",))
    graph = SimpleNamespace(pages=(SimpleNamespace(nodes=(n,)),))
    block = SimpleNamespace(candidate_id="a", segments=(n,))
    second = SimpleNamespace(candidate_id="b", segments=(n,))
    assert audit_owned_blocks(graph, [block])["owned_nodes"] == 1
    with pytest.raises(ValueError, match="more than one course"):
        audit_owned_blocks(graph, [block, second])
    altered = SimpleNamespace(node_id=n.node_id, raw_text="invented text", evidence=n.evidence)
    with pytest.raises(ValueError, match="differs from its source"):
        audit_owned_blocks(graph, [SimpleNamespace(candidate_id="c", segments=(altered,))])
