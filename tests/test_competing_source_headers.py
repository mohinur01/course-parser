# Tests for competing course headings and the text boundaries that must remain unresolved.
"""Actual exposed TUNING sources reproduce competing title claims."""

from pathlib import Path

import pytest
from test_source_regressions import replay_case

from course_parser.contextual import ContextualHeaderDetector, LineStyle
from course_parser.io import read_json
from course_parser.ownership import course_field_links

ROOT = Path(__file__).parent / "data/source_regressions_v4"
CASES = [
    (ROOT / "body_inventory_borrows_next_header.json", "MU160W", "MU170"),
    (ROOT / "prerequisite_borrows_next_header.json", "CHGN428.", "CHGN430."),
]


# Check that competing source header is withheld without double ownership.
@pytest.mark.parametrize("path,reference_code,next_code", CASES)
def test_competing_source_header_is_withheld_without_double_ownership(
    reference, path, reference_code, next_code
):
    graph, candidates, decisions, blocks, courses, _ = replay_case(
        reference, path, return_stages=True
    )
    conflicts = [
        c for c in candidates if "TITLE_OVERLAPS_COMPETING_HEADER" in c.contradictions
    ]
    conflict = next(c for c in conflicts if c.code_raw == reference_code)
    assert str(conflict.status) == "UNRESOLVED"
    assert all(
        str(d.status) != "INDEPENDENT_COURSE"
        for d in decisions
        if d.candidate_id == conflict.candidate_id
    )
    assert any(c.code.raw.strip().rstrip(".") == next_code.rstrip(".") for c in courses)
    assert course_field_links(graph, blocks, courses)
    # Sharing one printed heading is distinct from borrowing a later heading.
    assert not any(c.code_raw.startswith("MU141") for c in conflicts)
    if reference_code == "MU160W":
        assert not any(c.code.raw.startswith("MU160P") for c in courses)
        embedded = next(c for c in candidates if c.code_raw == "MU160P")
        assert any(
            d.candidate_id == embedded.candidate_id
            and "SOURCE_EMBEDDED_COMMA_SEPARATED_CODE_LIST" in d.reason_codes
            for d in decisions
        )


# Check that competing ownership is invariant to page scale and translation.
@pytest.mark.parametrize("path,reference_code,next_code", CASES)
def test_competing_ownership_is_invariant_to_page_scale_and_translation(
    reference, path, reference_code, next_code
):
    graph, candidates, _, _, _, styles = replay_case(reference, path, return_stages=True)
    raw = graph.model_dump(mode="json")
    for page in raw["pages"]:
        page["width"] = page["width"] * 3 + 300
        page["height"] = page["height"] * 3 + 200
        for node in page["nodes"]:
            for anchor in node["evidence"]:
                b = anchor["geometry"]
                if b:
                    for key in ("x0", "x1"):
                        b[key] = b[key] * 3 + 150
                    for key in ("y0", "y1"):
                        b[key] = b[key] * 3 + 100
    transformed = reference.models.DocumentGraph.model_validate(raw)
    scaled_styles = {
        nid: LineStyle(bold=s.bold, italic=s.italic, size=s.size * 3)
        for nid, s in styles.items()
    }
    # Use the same source-profile proposal; no institution/coordinate parameter is allowed to
    # change. The source identities and spans remain unchanged.
    import importlib

    profile = importlib.import_module(
        "catalog_mining.course_extraction_v3.document_grammar"
    ).DeterministicDocumentGrammarProfiler().profile(transformed)
    detector = ContextualHeaderDetector(reference.models, scaled_styles)
    scaled = detector.detect(transformed, profile)
    original = {c.candidate_id: str(c.status) for c in candidates}
    assert {c.candidate_id: str(c.status) for c in scaled} == original
    assert any(
        c["reason_code"] == "TITLE_OVERLAPS_COMPETING_HEADER"
        for c in detector.ownership_conflicts
    )


# Check that reviewed inventory has source supported complete courses.
def test_reviewed_inventory_has_source_supported_complete_courses(reference):
    path = CASES[0][0]
    assert read_json(path)["review_complete"] is True
    replay_case(reference, path)
