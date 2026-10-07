# Tests for older numeric course headings and the extra evidence needed to accept them.
"""Numeric identifier cues preserve source bytes and weaker candidates abstain."""

from pathlib import Path

import pytest
from test_contextual import extract
from test_source_regressions import replay_case

from course_parser.contextual import ContextualHeaderDetector, LineStyle
from course_parser.historical_headers import EXTENDED_SIGNAL, PAIR_SIGNAL
from course_parser.ownership import course_field_links

CASE = (
    Path(__file__).parent
    / "data/source_regressions_v6/numeric_pair_before_inline_description.json"
)


# Use the reviewed numeric-heading example to check where its title ends, and keep the
# parenthesized pair out of credits without a source legend.
def test_source_reviewed_numeric_pair_delimiter(reference):
    replay_case(reference, CASE)
    graph, candidates, decisions, blocks, records, _ = replay_case(
        reference, CASE, return_stages=True
    )
    first = next(c for c in records if c.code.raw == "135")
    assert first.title.raw == "ADVANCED FOOD PREPARATION"
    assert first.description.raw.strip() == (
        "Scientific principles and fundamental\n"
        "processes underlying food preparation and preservation with practical applications."
    )
    assert first.credits.raw is None  # No source legend establishing credit meaning.
    links = course_field_links(graph, blocks, records)
    pair = next(
        link
        for link in links
        if link["header_candidate_id"] == first.header_candidate_id
        and link["raw_text"] == "(2-4)"
    )
    assert pair["role"] == "OTHER_METADATA"
    assert pair["record_fields"] == []
    probe = next(c for c in candidates if c.code_raw == "2381")
    assert probe.code_slice.observed_text == "2381"  # Never guess away a footnote/OCR glyph.
    assert EXTENDED_SIGNAL in probe.structural_signals
    assert str(probe.status) == "UNRESOLVED"
    assert str(next(d for d in decisions if d.candidate_id == probe.candidate_id).status) == (
        "UNRESOLVED"
    )
    assert not any(c.header_candidate_id == probe.candidate_id for c in records)
    preceding = next(c for c in records if c.code.raw == "236")
    assert "WORLD DIETARY" not in preceding.description.raw
    assert str(preceding.boundary_status) == "UNRESOLVED_BOUNDARY"


# Check that source numeric cues do not depend on absolute font size.
@pytest.mark.parametrize("font_scale", [0.6, 2.5])
def test_source_numeric_cues_do_not_depend_on_absolute_font_size(reference, font_scale):
    baseline = replay_case(reference, CASE)
    scaled = replay_case(reference, CASE, font_scale=font_scale)
    fields = ("code", "title", "description", "credits", "prerequisites")
    assert [tuple(c[n]["raw"] for n in fields) for c in scaled] == [
        tuple(c[n]["raw"] for n in fields) for c in baseline
    ]


# Check that numeric pair is not assumed to be credits.
def test_numeric_pair_is_not_assumed_to_be_credits(reference):
    records, headers, _ = extract(
        reference,
        [
            "135 FIELD PRACTICE (2-4) Students examine deposits and interpret their history.",
            "137 RESOURCE STUDIES (1-5) Students map resources and describe their formation.",
        ],
    )
    assert len(records) == 2
    assert all(PAIR_SIGNAL in c.structural_signals for c in headers)
    assert [c.title.raw for c in records] == ["FIELD PRACTICE", "RESOURCE STUDIES"]
    assert all(c.description.raw.startswith("Students") for c in records)
    assert all(c.credits.raw is None for c in records)


# Check that numeric pairs and prose cannot override negative containers.
@pytest.mark.parametrize("heading", ["Degree Requirements", "Faculty", "Index"])
def test_numeric_pairs_and_prose_cannot_override_negative_containers(reference, heading):
    records, _, _ = extract(
        reference,
        [
            "135 FIELD PRACTICE (2-4) Students examine deposits and interpret their history.",
            "137 RESOURCE STUDIES (1-5) Students map resources and describe their formation.",
            "2381 WORLD RESOURCES Students study sources and report on their use.",
        ],
        heading=heading,
    )
    assert records == ()


# Check that numeric pair without inline prose does not supply the new delimiter cue.
def test_numeric_pair_without_inline_prose_does_not_supply_the_new_delimiter_cue(reference):
    _, headers, _ = extract(reference, ["135 FIELD PRACTICE (2-4)", "137 RESOURCE STUDIES (1-5)"])
    assert all(PAIR_SIGNAL not in c.structural_signals for c in headers)


# Check that extended number and title are only a weak boundary probe.
def test_extended_number_and_title_are_only_a_weak_boundary_probe(reference):
    records, _, headers, decisions, *_ = extract(
        reference,
        [
            "135 FIELD PRACTICE Students examine deposits and interpret their history.",
            "2381 WORLD RESOURCES Students study sources and report on their use.",
            "137 RESOURCE STUDIES Students map resources and describe their formation.",
        ],
        return_stages=True,
    )
    probe = next(c for c in headers if c.code_raw == "2381")
    assert probe.status == reference.models.HeaderStatus.UNRESOLVED
    assert str(next(d for d in decisions if d.candidate_id == probe.candidate_id).status) == (
        "UNRESOLVED"
    )
    assert {c.code.raw for c in records} == {"135", "137"}
    assert "WORLD RESOURCES" not in next(c for c in records if c.code.raw == "135").description.raw


# Check that modern numeric ranges keep their existing contract.
def test_modern_numeric_ranges_keep_their_existing_contract(reference):
    records, headers, _ = extract(
        reference,
        [
            "GEOL 101 Resource Geology (2-4)",
            "Students examine deposits and interpret their history.",
        ],
    )
    assert all(PAIR_SIGNAL not in c.structural_signals for c in headers)
    assert len(records) == 1
    assert records[0].title.raw.strip() == "Resource Geology"
    assert records[0].credits.raw.strip() == "(2-4)"


# Check that bare numbers and reference fragments are not extended header probes.
@pytest.mark.parametrize("raw", ["535", "535 (3)", "2026 ANNUAL REPORT", "2381 or 2390."])
def test_bare_numbers_and_reference_fragments_are_not_extended_header_probes(reference, raw):
    _, headers, _ = extract(reference, [raw])
    assert all(EXTENDED_SIGNAL not in c.structural_signals for c in headers)


# Check that extended numeric probes require other source seeds for promotion.
@pytest.mark.parametrize("count", [1, 2, 3])
def test_extended_numeric_probes_require_other_source_seeds_for_promotion(reference, count):
    import importlib

    lines = [
        f"{4300+i} FIELD METHODS Students inspect the source and describe the results."
        for i in range(count)
    ]
    _, graph, *_ = extract(reference, lines, return_stages=True)
    styles = {
        n.node_id: LineStyle(
            bold=True,
            size=12,
            bold_prefix_end=n.raw_text.index(" Students") if " Students" in n.raw_text else None,
        )
        for n in graph.pages[0].nodes
    }
    profile = (
        importlib.import_module("catalog_mining.course_extraction_v3.document_grammar")
        .DeterministicDocumentGrammarProfiler()
        .profile(graph)
    )
    candidates = ContextualHeaderDetector(reference.models, styles).detect(graph, profile)
    probes = [c for c in candidates if EXTENDED_SIGNAL in c.structural_signals]
    assert len(probes) == count
    assert all(str(c.status) == ("SUPPORTED" if count == 3 else "UNRESOLVED") for c in probes)
