# Tests for the evidence used to compare possible course endings and continuations.
"""Scores cannot override source proof or confuse fields with course extents."""

from pathlib import Path

import pytest
from test_contextual import extract
from test_source_regressions import replay_case

from course_parser.boundary_proposals import select_verified
from course_parser.boundary_scoring import BoundaryEvidence, ranking_summary
from course_parser.contextual import LineStyle, replace_validated


# Replay a pinned case through detection, contextual ownership and boundary scoring.
def stages(reference):
    return extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Students examine minerals and their geological setting.",
            "Prerequisite: GEOL 100.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and measurements.",
        ],
        return_stages=True,
    )


# Check that prerequisite proposal is a field stop and preserves course.
def test_prerequisite_proposal_is_a_field_stop_and_preserves_course(reference):
    records, _, _, _, _, resolver, _ = stages(reference)
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert "Prerequisite: GEOL 100." in first.raw_course_block
    alternatives = [p for r in resolver.boundary_proposals for p in r["proposals"]]
    internal = next(p for p in alternatives if p["scope"] == "DESCRIPTION")
    assert internal["status"] == "REVIEW_REQUIRED"
    assert internal["changes_owned_extent"] is False
    assert internal["ranking"]["family_scores"]["field"] == 4


# Check that rejected next course continuation cannot win a score competition.
def test_rejected_next_course_continuation_cannot_win_a_score_competition(reference):
    _, _, _, _, _, resolver, _ = stages(reference)
    first = resolver.boundary_proposals[0]
    rejected = [i for i, p in enumerate(first["proposals"]) if p["status"] == "REJECTED"]
    assert rejected
    assert set(rejected).issubset(first["ranking"]["excluded_from_acceptance"])
    assert first["ranking"]["decision"] == "ACCEPT_UNIQUE_VERIFIED_ENDPOINT"
    assert len(first["ranking"]["admissible_endpoint_groups"]) == 1
    assert len(first["ranking"]["admissible_endpoint_groups"][0]["proposal_indices"]) == 3


# Check that provisional large margin does not resolve conflicting verified extents.
def test_provisional_large_margin_does_not_resolve_conflicting_verified_extents():
    report = ranking_summary(
        [
            {"status": "ADMISSIBLE", "extent_key": "a", "ranking": {"score": 10}},
            {"status": "ADMISSIBLE", "extent_key": "b", "ranking": {"score": 1}},
            {"status": "REJECTED", "extent_key": "c", "ranking": {"score": 100}},
        ]
    )
    assert report["top_score_margin"] == 9
    assert report["decision"] == "REVIEW_REQUIRED"
    assert report["excluded_from_acceptance"] == [2]


# Check that endpoint proof methods do not create a false conflict.
def test_endpoint_proof_methods_do_not_create_a_false_conflict(reference):
    _, _, _, _, blocks, _, _ = stages(reference)
    first = blocks[0]
    same_extent = replace_validated(
        first, boundary_kind=reference.models.BoundaryKind.SECTION_BOUNDARY
    )
    # Only admitted blocks reach selection; validation of each proving method belongs to
    # boundary_violations, not this identity grouping helper.
    chosen, reason = select_verified(first, (first, same_extent), reference.models)
    assert reason == "UNIQUE_ADMISSIBLE_ENDPOINT"
    assert chosen == first


# Check that typography missing is unknown and geometry is scale invariant.
def test_typography_missing_is_unknown_and_geometry_is_scale_invariant(reference):
    _, graph, candidates, decisions, _, _, _ = stages(reference)
    m = reference.models
    scorer = BoundaryEvidence(graph, candidates, decisions, m)
    score = scorer.score(scorer.nodes["n2"], scorer.nodes["n3"])
    assert "TYPOGRAPHY_UNAVAILABLE" in score["unavailable"]
    factor = 3.7
    pages = []
    for page in graph.pages:
        nodes = []
        for node in page.nodes:
            anchors = tuple(
                replace_validated(
                    a,
                    geometry=replace_validated(
                        a.geometry,
                        **{k: getattr(a.geometry, k) * factor for k in ("x0", "y0", "x1", "y1")},
                    ),
                )
                for a in node.evidence
            )
            nodes.append(replace_validated(node, evidence=anchors))
        pages.append(
            replace_validated(
                page, nodes=tuple(nodes), width=page.width * factor, height=page.height * factor
            )
        )
    scaled = BoundaryEvidence(
        replace_validated(graph, pages=tuple(pages)), candidates, decisions, m
    )
    other = scaled.score(scaled.nodes["n2"], scaled.nodes["n3"])
    assert score["score"] == other["score"]
    assert score["family_scores"] == other["family_scores"]


# Check that large internal gap does not truncate an owned course.
def test_large_internal_gap_does_not_truncate_an_owned_course(reference):
    records, _, _, _, _, resolver, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Students examine geological settings.",
            "They investigate related field evidence.",
            "GEOL 102 Exploration Methods",
            "Students investigate field methods and measurements.",
        ],
        geometry_by_index={3: dict(x0=30, x1=450, y0=110, y1=120)},
        return_stages=True,
    )
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert "They investigate related field evidence." in first.raw_course_block
    proposals = [p for r in resolver.boundary_proposals for p in r["proposals"]]
    assert any(
        p["origin"] == "LARGE_RELATIVE_GAP" and p["status"] == "REVIEW_REQUIRED" for p in proposals
    )


# Check that language and layout cues never masquerade as probabilities.
def test_language_and_layout_cues_never_masquerade_as_probabilities(reference):
    _, graph, candidates, decisions, _, _, _ = stages(reference)
    styles = {"n2": LineStyle(size=10), "n3": LineStyle(size=12, italic=True)}
    scorer = BoundaryEvidence(graph, candidates, decisions, reference.models, styles)
    score = scorer.score(scorer.nodes["n2"], scorer.nodes["n3"])
    assert score["family_scores"]["typography"] == 1
    assert score["family_scores"]["field"] == 0  # Full course includes prerequisites.
    assert "UNCALIBRATED" in score["score_kind"]
    assert score["evidence"]


# Check that reviewed catalog metadata is ranked without rewriting ownership.
@pytest.mark.parametrize(
    "folder,filename",
    [
        ("source_regressions_v2", "credit_and_metadata.json"),
        ("source_regressions_v3", "marked_historical_codes_and_metadata.json"),
    ],
)
def test_reviewed_catalog_metadata_is_ranked_without_rewriting_ownership(
    reference, folder, filename
):
    graph, candidates, decisions, blocks, records, styles = replay_case(
        reference, Path(__file__).parent / "data" / folder / filename, return_stages=True
    )
    scorer = BoundaryEvidence(graph, candidates, decisions, reference.models, styles)
    frozen = [r.model_dump(mode="json") for r in records]
    hypotheses = [p for b in blocks for p in scorer.review_alternatives(b)]
    prerequisites = [p for p in hypotheses if p["scope"] == "DESCRIPTION"]
    assert prerequisites
    for p in prerequisites:
        assert p["status"] == "REVIEW_REQUIRED"
        assert p["ranking"]["family_scores"]["field"] == 4
        assert p["ranking"]["evidence"]
        assert p["changes_owned_extent"] is False
    assert frozen == [r.model_dump(mode="json") for r in records]
