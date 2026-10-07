# Tests for replaying approved catalog examples and checking the existing course recovery
# guarantees.
"""Replay actual source-reviewed catalogs; no predicted answer keys."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from regression_catalogue import catalogue, check_replay_contract, fixture_cases

from course_parser.contextual import (
    ContextualContinuationResolver,
    ContextualCourseFieldParser,
    ContextualEntryClassifier,
    ContextualHeaderDetector,
    LineStyle,
    contextual_layout,
)
from course_parser.io import read_json, verify_files
from course_parser.learning_evaluation import align_headers, complete_course_agrees
from course_parser.ownership import OwnedCourseBlockBuilder, audit_owned_blocks
from course_parser.page_accounting import build_page_accounting
from course_parser.table_structure import layout_conservation, table_contexts

ROOT = Path(__file__).parent / "data/source_regressions_v2"


# Replay the approved catalog cases and check the fields and source text belonging to each
# course.
@pytest.mark.parametrize("case_path", fixture_cases("source_regressions_v2"), ids=lambda p: p.stem)
def test_source_owned_catalog_cases(reference, case_path):
    replay_case(reference, case_path)


# Reproduce the saved extraction diagnostics on the approved exposed transfer sample.
@pytest.mark.parametrize(
    "case_path",
    fixture_cases("source_regressions_v3"),
    ids=lambda p: p.stem,
)
def test_exposed_transfer_diagnostics(reference, case_path):
    case = read_json(case_path)
    assert case["exposure_scope"] == "EXPOSED_DIAGNOSTIC_AT_USER_REQUEST_AFTER_FROZEN_AUDIT"
    original = replay_case(reference, case_path)
    # Relative typography must give the same fields at a different font scale.
    scaled = replay_case(reference, case_path, font_scale=2.5)
    fields = ("code", "title", "description", "credits", "prerequisites")
    assert [tuple(c[name]["raw"] for name in fields) for c in original] == [
        tuple(c[name]["raw"] for name in fields) for c in scaled
    ]


# Run the frozen source case and compare its recovery, ownership and ambiguity with the
# existing contract.
def replay_case(reference, case_path, *, font_scale=1.0, return_stages=False):
    assert case_path.resolve() in fixture_cases(case_path.parent.name)
    manifest = read_json(case_path.parent / "manifest.json")
    verify_files(case_path.parent, manifest["file_sha256"])
    case = read_json(case_path)
    item = case["selected"]
    if "exposure_scope" not in case:
        assert item["learning_partition"] == "TUNING"
    assert case["truth_basis"] == "EXISTING_SOURCE_IMAGE_REVIEW_NOT_PARSER_OUTPUT"
    m = reference.models
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    source_layouts = tuple(
        module.PageLayoutProposal.model_validate(p) for p in case["source_layouts"]
    )
    assert all(p.source_pdf_checksum == item["source_pdf_checksum"] for p in source_layouts)
    styles = {
        nid: LineStyle(**{**style, "size": style["size"] * font_scale})
        for nid, style in case["styles"].items()
    }
    layouts = tuple(
        contextual_layout(p, module, m, ordering="source_rows", styles=styles)
        for p in source_layouts
    )
    for source, adopted in zip(source_layouts, layouts, strict=True):
        assert layout_conservation(source, adopted)["status"] == "PASS"
    graph = m.DocumentGraph(
        identity=m.SourceDocumentIdentity(
            source_document_id=item["source_document_id"],
            source_pdf_checksum=item["source_pdf_checksum"],
            source_pdf_path=item["source_pdf_path"],
        ),
        pages=tuple(module.proposal_to_page_graph(p) for p in layouts),
    )
    profile = (
        importlib.import_module("catalog_mining.course_extraction_v3.document_grammar")
        .DeterministicDocumentGrammarProfiler()
        .profile(graph)
    )
    candidates = ContextualHeaderDetector(m, styles).detect(graph, profile)
    decisions = ContextualEntryClassifier(
        m, styles, table_contexts=table_contexts(layouts)
    ).classify(graph, profile, candidates)
    blocks = OwnedCourseBlockBuilder(m).build(graph, profile, candidates, decisions)
    blocks = ContextualContinuationResolver(m).resolve(
        graph, profile, candidates, decisions, blocks
    )
    audit_owned_blocks(graph, blocks)
    courses = ContextualCourseFieldParser(m, styles).parse(graph, profile, candidates, blocks)
    source_graph = m.DocumentGraph(
        identity=graph.identity,
        pages=tuple(module.proposal_to_page_graph(p) for p in source_layouts),
    )
    accounting = build_page_accounting(
        graph,
        blocks,
        courses,
        source_graph=source_graph,
        candidates=candidates,
        decisions=decisions,
        table_contexts=table_contexts(layouts),
    )
    assert accounting["audit"]["status"] == "PASS"
    if return_stages:
        return graph, candidates, decisions, blocks, courses, styles
    assert case.get("review_complete", True), "partial source observations are not scored truth"
    central = [c for c in candidates if c.physical_page == item["central_physical_page"]]
    gold = [g for g in case["gold"] if g["scope"] == "HEADER_STARTS_ON_CENTRAL_PAGE"]
    matched, ambiguous = align_headers([c.model_dump(mode="json") for c in central], gold)
    actual = {c.header_candidate_id: c.model_dump(mode="json") for c in courses}
    expected = {g["gold_course_id"]: g for g in gold}
    recovered = {
        gid
        for cid, gid in matched.items()
        if cid in actual and complete_course_agrees(actual[cid], expected[gid])
    }
    assert len(recovered) >= case["minimum_complete_content"]
    contract = catalogue()["source_case_contracts"][f"{case_path.parent.name}/{case_path.name}"]
    unaligned = ({c.candidate_id for c in central} & actual.keys()) - matched.keys()
    check_replay_contract(contract, recovered, unaligned, ambiguous)
    if not gold:
        assert not {c.candidate_id for c in central} & set(actual)
    return [actual[c.candidate_id] for c in central if c.candidate_id in actual]
