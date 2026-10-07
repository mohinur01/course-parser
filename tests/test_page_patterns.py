# Tests for using nearby course headings as supporting evidence without overriding negative
# context.
"""Local source patterns must transfer under scale and preserve hard negatives."""

from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_contextual import extract
from test_source_regressions import replay_case

from course_parser.contextual import (
    ContextualEntryClassifier,
    ContextualHeaderDetector,
    LineStyle,
    native_bold_prefix_end,
    replace_validated,
)
from course_parser.ownership import course_field_links

CASE = Path(__file__).parent / "data/source_regressions_v5/numeric_inline_native_font_boundary.json"


# Build the layout profile used to interpret the synthetic course candidates.
def profile(graph):
    return (
        importlib.import_module("catalog_mining.course_extraction_v3.document_grammar")
        .DeterministicDocumentGrammarProfiler()
        .profile(graph)
    )


# Build a small source neighborhood with historical numeric headers and nearby supporting
# evidence.
def local_fixture(reference, count=3, *, heading="Course Descriptions", credits=True):
    lines = [
        f"{4300 + i} Course Topic {i}"
        + (" (3)" if credits else "")
        + ". Students inspect the source and describe the results."
        for i in range(count)
    ]
    _, graph, *_ = extract(reference, lines, heading=heading, return_stages=True)
    styles = {
        n.node_id: LineStyle(
            bold=True,
            size=11,
            bold_prefix_end=n.raw_text.index(". Students") if ". Students" in n.raw_text else None,
        )
        for n in graph.pages[0].nodes
    }
    return graph, styles


# Look for possible course headings using their printed text and nearby page context.
def detect(reference, graph, styles):
    detector = ContextualHeaderDetector(reference.models, styles)
    candidates = detector.detect(graph, profile(graph))
    return detector, [c for c in candidates if "LOCAL_PAGE_NUMERIC_PATTERN" in c.structural_signals]


# Check that source reviewed inline numeric courses are complete.
def test_source_reviewed_inline_numeric_courses_are_complete(reference):
    courses = replay_case(reference, CASE)
    assert len(courses) >= 20
    graph, _, _, blocks, records, _ = replay_case(reference, CASE, return_stages=True)
    assert course_field_links(graph, blocks, records)
    first = next(c for c in records if c.code.raw == "4300")
    assert first.title.raw.strip() == "Restorative Dentistry Practicum"
    assert first.credits.raw.strip() == "(2)"
    assert first.description.raw.startswith("Students are assigned")
    assert "4360" not in next(c for c in records if c.code.raw == "4350").description.raw
    _, _, _, _, _, styles = replay_case(reference, CASE, return_stages=True)
    detector, _ = detect(reference, graph, styles)
    json.dumps(detector.page_pattern_proposals)


# Check that pattern requires two other distinct source entries.
def test_pattern_requires_two_other_distinct_source_entries(reference):
    for count in (1, 2, 3):
        graph, styles = local_fixture(reference, count)
        detector, local = detect(reference, graph, styles)
        assert len(local) == (3 if count == 3 else 0)
        for proposal in detector.page_pattern_proposals:
            assert proposal["source_truth_read"] is False
            for observation in proposal["observations"]:
                if observation["adopted"]:
                    peers = observation["other_supporting_node_ids"]
                    assert len(peers) >= 2 and observation["node_id"] not in peers


# Check that repeated display shapes do not override negative containers.
@pytest.mark.parametrize("heading", ["Degree Requirements", "Faculty Directory", "Index"])
def test_repeated_display_shapes_do_not_override_negative_containers(reference, heading):
    graph, styles = local_fixture(reference, heading=heading)
    _, local = detect(reference, graph, styles)
    assert not local


# Check that unscoped repeated years are not course pattern seeds.
def test_unscoped_repeated_years_are_not_course_pattern_seeds(reference):
    graph, styles = local_fixture(reference, heading="History", credits=False)
    _, local = detect(reference, graph, styles)
    assert not local


# Check that creditless courses can use an explicit inventory pattern.
def test_creditless_courses_can_use_an_explicit_inventory_pattern(reference):
    graph, styles = local_fixture(reference, credits=False)
    _, local = detect(reference, graph, styles)
    assert len(local) == 3
    decisions = ContextualEntryClassifier(reference.models, styles).classify(
        graph, profile(graph), tuple(local)
    )
    assert all(str(d.status) == "INDEPENDENT_COURSE" for d in decisions)


# Leave pattern support unresolved when native font runs are missing or misaligned.
def test_missing_or_misaligned_native_runs_abstain(reference):
    graph, styles = local_fixture(reference)
    styles = {nid: replace(s, bold_prefix_end=None) for nid, s in styles.items()}
    assert not detect(reference, graph, styles)[1]
    assert (
        native_bold_prefix_end(
            {
                "text": "4300 Mineral Systems",
                "spans": [{"text": "4300Mineral Systems", "flags": 16}],
            }
        )
        is None
    )


# Check that local pattern preserves printed identifier punctuation.
def test_local_pattern_preserves_printed_identifier_punctuation(reference):
    graph, _ = local_fixture(reference)
    m = reference.models
    nodes, styles = [], {}
    for node in graph.pages[0].nodes:
        raw = node.raw_text
        if ". Students" in raw:
            code, tail = raw.split(" ", 1)
            raw = code + ". " + tail
        anchors = tuple(replace_validated(a, observed_text=raw) for a in node.evidence)
        node = replace_validated(node, raw_text=raw, evidence=anchors)
        nodes.append(node)
        styles[node.node_id] = LineStyle(
            bold=True,
            size=11,
            bold_prefix_end=raw.index(". Students") if ". Students" in raw else None,
        )
    graph = replace_validated(graph, pages=(replace_validated(graph.pages[0], nodes=tuple(nodes)),))
    _, local = detect(reference, graph, styles)
    assert {c.code_raw for c in local} == {"4300.", "4301.", "4302."}
    assert all(c.code_slice.observed_text.endswith(".") for c in local)
    assert all(c.identifier_kind == m.HeaderIdentifierKind.HISTORICAL_NUMERIC for c in local)


# Check that local support cannot cross sections columns or uncertain tables.
@pytest.mark.parametrize("split_kind", ["section", "column", "table"])
def test_local_support_cannot_cross_sections_columns_or_uncertain_tables(reference, split_kind):
    graph, styles = local_fixture(reference)
    m = reference.models
    page = graph.pages[0]
    if split_kind == "section":
        # The middle line is an actual new structural section, not a third seed.
        nodes = tuple(
            replace_validated(n, role=m.LayoutNodeRole.SECTION_HEADING) if n.node_id == "n2" else n
            for n in page.nodes
        )
        page = replace_validated(page, nodes=nodes)
    elif split_kind == "column":
        edges = tuple(
            e
            for e in page.edges
            if not (e.kind == m.EdgeKind.SAME_COLUMN and e.target_node_id == "n3")
        )
        page = replace_validated(page, edges=edges)
    else:
        nodes = tuple(
            replace_validated(n, role=m.LayoutNodeRole.TABLE_CELL)
            if n.node_id in {"n1", "n2", "n3"}
            else n
            for n in page.nodes
        )
        page = replace_validated(page, nodes=nodes)
    graph = replace_validated(graph, pages=(page,))
    assert not detect(reference, graph, styles)[1]


# Check that page pattern is invariant to geometry scale and translation.
def test_page_pattern_is_invariant_to_geometry_scale_and_translation(reference):
    graph, _, _, _, _, styles = replay_case(reference, CASE, return_stages=True)
    _, original = detect(reference, graph, styles)
    raw = graph.model_dump(mode="json")
    for page in raw["pages"]:
        page["width"] *= 3
        page["height"] *= 3
        for node in page["nodes"]:
            for anchor in node["evidence"]:
                b = anchor["geometry"]
                if b:
                    for k in ("x0", "x1"):
                        b[k] = b[k] * 3 + 150
                    for k in ("y0", "y1"):
                        b[k] = b[k] * 3 + 100
    transformed = reference.models.DocumentGraph.model_validate(raw)
    _, scaled = detect(
        reference, transformed, {nid: replace(s, size=s.size * 3) for nid, s in styles.items()}
    )

    # Return the candidate source pattern used to compare independently built examples.
    def signature(rows):
        return {(c.candidate_id, c.code_raw, c.title_raw) for c in rows}

    assert signature(original) == signature(scaled)
