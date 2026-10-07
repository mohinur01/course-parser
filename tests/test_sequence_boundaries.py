# Tests for field-sequence rules and competing course endings.
"""Adversarial whole-sequence and competing-source-boundary regressions."""

from dataclasses import replace

from test_contextual import extract

from course_parser.boundary_proposals import boundary_violations, select_verified
from course_parser.contextual import replace_validated
from course_parser.sequence_constraints import (
    SequenceState,
    SpanObservation,
    decode_spans,
    transition,
)


# Build an exact source-text span for the sequence-transition tests.
def span(role, **values):
    return SpanObservation(allocation_id=role, proposed_role=role, **values)


# Check that fields cannot restart a header or start unlabelled metadata.
def test_fields_cannot_restart_a_header_or_start_unlabelled_metadata():
    body = SequenceState(body_started=True, last_role="DESCRIPTION")
    assert transition(body, "TITLE", span("TITLE", header_anchor=True))[1] == (
        "HEADER_CANNOT_RESTART_INSIDE_OWNED_BODY"
    )
    assert transition(body, "PREREQUISITES", span("PREREQUISITES"))[1] == (
        "PREREQUISITES_REQUIRE_LABEL_OR_CONTINUATION"
    )
    assert transition(body, "CREDITS", span("CREDITS"))[1] == (
        "CREDITS_REQUIRE_PRINTED_UNIT_OR_HEADER_ROW"
    )
    assert transition(body, "CREDITS", span("CREDITS", credit_marker=True))[1] is None
    assert (
        transition(body, "PREREQUISITES", span("PREREQUISITES", prerequisite_marker=True))[1]
        is None
    )


# Check that credits can precede title and body and credits can be absent.
def test_credits_can_precede_title_and_body_and_credits_can_be_absent():
    observations = (
        span("CODE", header_anchor=True),
        span("CREDITS", credit_marker=True),
        span("TITLE", header_anchor=True),
        span("DESCRIPTION"),
    )
    assert decode_spans(observations)["status"] == "UNIQUE"
    assert (
        decode_spans(tuple(s for s in observations if s.proposed_role != "CREDITS"))["status"]
        == "UNIQUE"
    )


# Check that dynamic programming retains a prefix needed for a legal future.
def test_dynamic_programming_retains_a_prefix_needed_for_a_legal_future():
    report = decode_spans(
        (
            span("CODE", header_anchor=True),
            span("OTHER_METADATA", alternatives=("PREREQUISITES",), prerequisite_marker=True),
            span("PREREQUISITES"),
        )
    )
    assert report["status"] == "UNIQUE"
    assert report["selected_roles"] == ["CODE", "PREREQUISITES", "PREREQUISITES"]
    assert any(
        r["reason"] == "PREREQUISITES_REQUIRE_LABEL_OR_CONTINUATION"
        for r in report["rejected_transitions"]
    )


# Check that tied field paths are retained without inventing a confidence.
def test_tied_field_paths_are_retained_without_inventing_a_confidence():
    report = decode_spans(
        (
            span("CODE", header_anchor=True),
            span("PREREQUISITES", prerequisite_marker=True),
            span("PREREQUISITES", alternatives=("DESCRIPTION",), narrative_restart=True),
        )
    )
    assert report["status"] == "AMBIGUOUS"
    assert report["selected_roles"] is None
    assert len(report["paths"]) == 2
    assert report["affected_roles"] == ["DESCRIPTION", "PREREQUISITES"]
    assert "PROBABILITY" in report["score_kind"]


# Check that ambiguous paragraph after prerequisite withholds fields but keeps source.
def test_ambiguous_paragraph_after_prerequisite_withholds_fields_but_keeps_source(reference):
    records, _, _, _, _, _, fields = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "Prerequisite: GEOL 100.",
            "Evaluation of measurements and interpretation of results.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and measurements.",
        ],
        geometry_by_index={4: dict(x0=30, x1=450, y0=115, y1=125)},
        return_stages=True,
    )
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert first.description.status == "AMBIGUOUS"
    assert first.prerequisites.status == "AMBIGUOUS"
    assert "Evaluation of measurements" in first.raw_course_block
    assert any(s["status"] == "AMBIGUOUS" for s in fields.sequence_proposals)
    assert (
        "\n".join(dict.fromkeys(a.segment_raw_text for a in first.field_allocations))
        == first.raw_course_block
    )


# Check that boundary alternatives reject attaching next course and references.
def test_boundary_alternatives_reject_attaching_next_course_and_references(reference):
    _, _, _, _, _, resolver, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "Prerequisite: completed prior work in",
            "GEOL 099 or instructor permission.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and measurements.",
        ],
        return_stages=True,
    )
    assert resolver.boundary_proposals
    alternatives = [p for r in resolver.boundary_proposals for p in r["proposals"]]
    assert any(p["action"] == "CONTINUE" and p["status"] == "REJECTED" for p in alternatives)
    assert all(
        r["selected_boundary_status"] == "CONFIRMED_BOUNDARY" for r in resolver.boundary_proposals
    )


# Check that endpoint cannot skip intervening source and section label is not proof.
def test_endpoint_cannot_skip_intervening_source_and_section_label_is_not_proof(reference):
    _, graph, headers, decisions, blocks, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and measurements.",
        ],
        return_stages=True,
    )
    m = reference.models
    first = next(b for b in blocks if b.start_node_id == "n1")
    section = graph.pages[0].nodes[-1]
    fabricated_stop = replace_validated(
        first,
        boundary_kind=m.BoundaryKind.SECTION_BOUNDARY,
        boundary_node_ids=(section.node_id,),
        boundary_evidence=section.evidence,
    )
    owners = {nid: b.candidate_id for b in blocks for nid in b.node_ids}
    violations = boundary_violations(graph, fabricated_stop, headers, decisions, owners, m)
    assert "STOP_CANNOT_SKIP_UNOWNED_SOURCE_CONTENT" in violations


# Check that conflicting admitted endpoints abstain and preserve the prior extent.
def test_conflicting_admitted_endpoints_abstain_and_preserve_the_prior_extent(reference):
    _, graph, _, _, blocks, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of minerals and their geological setting.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and measurements.",
        ],
        return_stages=True,
    )
    m = reference.models
    first = next(b for b in blocks if b.start_node_id == "n1")
    section = graph.pages[0].nodes[-1]
    competing = replace_validated(
        first,
        boundary_kind=m.BoundaryKind.SECTION_BOUNDARY,
        boundary_node_ids=(section.node_id,),
        boundary_evidence=section.evidence,
    )
    withheld, reason = select_verified(first, (first, competing), m)
    assert reason == "CONFLICTING_ENDPOINTS_WITHHELD"
    assert withheld.boundary_status == m.BoundaryStatus.UNRESOLVED
    assert withheld.raw_text == first.raw_text
    assert withheld.block_id == first.block_id
    assert withheld.warnings[-1].code == "COMPETING_BOUNDARY_PROPOSALS"


# Check that unknown roles and unsupported description restarts have no legal path.
def test_unknown_roles_and_unsupported_description_restarts_have_no_legal_path():
    obs = span("PREREQUISITES", prerequisite_marker=True)
    report = decode_spans((obs, span("DESCRIPTION")))
    assert report["status"] == "NO_LEGAL_PATH"
    assert (
        decode_spans((replace(obs, proposed_role="INVENTED_LABEL"),))["status"] == "NO_LEGAL_PATH"
    )
