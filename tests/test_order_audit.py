# Tests for detecting reading-order disagreements over the same original text pieces.
"""Second opinions cannot become source truth or replace native ordering."""

import copy

import pytest

from course_parser.order_audit import compare_order_relations


# Build native and alternative layouts from one synthetic page for order comparisons.
def opinions(reference, *, reversed_order=False):
    from test_owned_order import native_proposal

    module, native = native_proposal(reference)

    # Require coordinate values to follow the expected order.
    def ordered(ids, engine):
        raw = native.model_dump(mode="json")
        raw["engine"] = engine
        raw["reading_order"] = [
            dict(
                source_region_id=a,
                target_region_id=b,
                status="CONFIRMED",
                reason="fixture order proposal",
                confidence=None,
            )
            for a, b in zip(ids, ids[1:], strict=False)
        ]
        return module.PageLayoutProposal.model_validate(raw)

    return (
        module,
        ordered(["n0", "n1", "n2"], "PYMUPDF_GEOMETRY"),
        ordered(["n0", "n2", "n1"] if reversed_order else ["n0", "n1", "n2"], "DOCLING"),
    )


# Check that identical order is agreement not certified truth.
def test_identical_order_is_agreement_not_certified_truth(reference):
    _, native, alternative = opinions(reference)
    result = compare_order_relations(native, alternative)
    assert result["decision"] == "ORDER_AGREEMENT"
    assert result["counts"]["agreement_pairs"] == result["pair_denominator"] == 3
    assert result["reversed_pair_fraction_conditional"] == 0
    assert not result["source_truth_read"] and not result["native_order_replaced"]
    assert not result["course_ownership_assigned"] and not result["text_replaced"]


# Flag a reversed reading-order link and retain its exact native source locations for review.
def test_reversed_relation_routes_review_with_exact_native_source_anchors(reference):
    _, native, alternative = opinions(reference, reversed_order=True)
    before = (native.model_dump(mode="json"), alternative.model_dump(mode="json"))
    result = compare_order_relations(native, alternative)
    assert result["review_flags"] == ["REVIEW_ORDER_DISAGREEMENT"]
    assert result["counts"]["reversed_pairs"] == 1
    assert result["counts"]["agreement_pairs"] == 2
    assert result["reversals"] == [
        dict(native_before="n1", native_after="n2", alternative_before="n2", alternative_after="n1")
    ]
    assert {r["region_id"] for r in result["affected_source_regions"]} == {"n1", "n2"}
    assert before == (native.model_dump(mode="json"), alternative.model_dump(mode="json"))


# Check that unresolved edges are barriers not inverse or implicit agreement.
def test_unresolved_edges_are_barriers_not_inverse_or_implicit_agreement(reference):
    module, native, alternative = opinions(reference)
    raw = alternative.model_dump(mode="json")
    raw["status"] = "PARTIAL"
    raw["reading_order"][0]["status"] = "UNRESOLVED"
    alternative = module.PageLayoutProposal.model_validate(raw)
    result = compare_order_relations(native, alternative)
    assert result["counts"]["comparable_pairs"] == 1
    assert result["counts"]["alternative_unresolved_pairs"] == 2
    assert result["counts"]["reversed_pairs"] == 0
    assert result["review_flags"] == ["REVIEW_EXTERNAL_ORDER_COVERAGE"]
    assert len(result["alternative_unresolved_edges"]) == 1


# Check that partial order branches do not invent sibling order.
def test_partial_order_branches_do_not_invent_sibling_order(reference):
    module, native, alternative = opinions(reference)
    raw = alternative.model_dump(mode="json")
    raw["reading_order"][1]["source_region_id"] = "n0"
    alternative = module.PageLayoutProposal.model_validate(raw)
    result = compare_order_relations(native, alternative)
    assert result["counts"]["comparable_pairs"] == 2
    assert result["counts"]["alternative_unresolved_pairs"] == 1
    assert not result["reversals"]


# Check that different source or frame is rejected.
@pytest.mark.parametrize(
    "field,value", [("physical_page", 2), ("source_pdf_checksum", "b" * 64), ("page_width", 600)]
)
def test_different_source_or_frame_is_rejected(reference, field, value):
    _, native, alternative = opinions(reference)
    with pytest.raises(ValueError, match="source pages or coordinate frames"):
        compare_order_relations(native, alternative.model_copy(update={field: value}))


# Check that lossy source projection cannot enter disagreement scoring.
@pytest.mark.parametrize("change", ["text", "geometry", "role", "deleted"])
def test_lossy_source_projection_cannot_enter_disagreement_scoring(reference, change):
    module, native, alternative = opinions(reference)
    raw = copy.deepcopy(alternative.model_dump(mode="json"))
    if change == "text":
        raw["regions"][1]["raw_text"] = "INVENTED MODEL TEXT"
    elif change == "geometry":
        raw["regions"][1]["geometry"]["x0"] += 1
    elif change == "role":
        raw["regions"][1]["role"] = "PAGE_FURNITURE"
    else:
        raw["regions"].pop()
        raw["reading_order"] = []
    with pytest.raises(ValueError, match="identical native cells"):
        compare_order_relations(native, module.PageLayoutProposal.model_validate(raw))


# Check that cycles are invalid even if both opinions agree.
def test_cycles_are_invalid_even_if_both_opinions_agree(reference):
    module, native, alternative = opinions(reference)
    raw = alternative.model_dump(mode="json")
    raw["reading_order"].append(
        dict(
            source_region_id="n2",
            target_region_id="n0",
            status="CONFIRMED",
            reason="fixture cycle",
            confidence=None,
        )
    )
    with pytest.raises(ValueError, match="cyclic"):
        compare_order_relations(native, module.PageLayoutProposal.model_validate(raw))


# Check that duplicate relations do not add votes.
def test_duplicate_relations_do_not_add_votes(reference):
    module, native, alternative = opinions(reference)
    raw = alternative.model_dump(mode="json")
    raw["reading_order"].append(copy.deepcopy(raw["reading_order"][0]))
    with pytest.raises(ValueError, match="duplicate reading-order"):
        compare_order_relations(native, module.PageLayoutProposal.model_validate(raw))


# Check that no native body source cannot claim agreement.
def test_no_native_body_source_cannot_claim_agreement(reference):
    module, native, alternative = opinions(reference)

    # Build an empty layout with an explicit geometry-unavailable status.
    def empty(p):
        raw = p.model_dump(mode="json")
        raw.update(regions=[], reading_order=[], status="GEOMETRY_UNAVAILABLE")
        return module.PageLayoutProposal.model_validate(raw)

    result = compare_order_relations(empty(native), empty(alternative))
    assert result["decision"] == "NO_BODY_SOURCE"
    assert result["pair_denominator"] == 0
    assert result["reversed_pair_fraction_conditional"] is None


# Check that contextual default needs no external engine and never selects one.
def test_contextual_default_needs_no_external_engine_and_never_selects_one(
    tmp_path, reference, monkeypatch
):
    from test_source_learning import _pilot_fixture

    from course_parser import engine_order
    from course_parser.io import read_json
    from course_parser.learning import run_learning_baseline

    pilot, _, _, _ = _pilot_fixture(tmp_path, reference)

    # Fail the test if the default native pipeline attempts to invoke an external ordering
    # engine.
    def external_must_not_run(*args, **kwargs):
        raise AssertionError("primary native ordering attempted an external replacement")

    monkeypatch.setattr(engine_order, "external_order", external_must_not_run)
    output = tmp_path / "native-default"
    result = run_learning_baseline(reference, pilot, output, contextual_course_assembly=True)
    assert result["failed_windows"] == 0
    manifest = read_json(output / "prediction_manifest.json")
    assert manifest["ordering_engine"] == "source_rows"
    assert manifest["engine_exports_manifest_sha256"] is None
    assert manifest["automatic_engine_order_selection"] is False
    assert manifest["source_truth_read"] is False
