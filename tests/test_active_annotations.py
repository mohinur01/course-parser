# Tests for selecting balanced annotation batches without reading answers or reserved data.
"""Acquisition provenance, exploration, coverage updates and whole-window integrity."""

import copy
import json
import random
from pathlib import Path

import pytest

from course_parser import active_annotations as module


# Build a synthetic annotation window with the chosen style and difficulty signals.
def window(i, **changes):
    return {
        "dataset_page_id": f"page-{i:02d}",
        "learning_partition": "TUNING",
        "family_id": f"family-{i % 6}",
        "source_pdf_checksum": f"pdf-{i}",
        "context_physical_pages": [1, 2, 3],
        "annotation_state": "UNLABELLED_SOURCE_READY",
        "difficulty_score": i % 4,
        "challenge_tags": [f"tag-{i % 5}"],
        "style_descriptor": f"style-{i % 5}",
        "adjudicated_style_windows": 0,
        "adjudicated_family_windows": 0,
        "source_inventory_style_windows": i % 3,
        **changes,
    }


# Check that default mixture and seeded random draw are preserved.
def test_default_mixture_and_seeded_random_draw_are_preserved():
    pool = [window(i) for i in range(30)]
    frozen = copy.deepcopy(pool)
    result = module.select_batch(pool)
    assert pool == frozen
    assert result["actual_stream_counts"] == {"RANDOM": 3, "FAILURES": 4, "STYLE_COVERAGE": 3}
    expected = random.Random(1501).sample(sorted(pool, key=lambda x: x["dataset_page_id"]), 3)
    assert [x["dataset_page_id"] for x in result["selected"][:3]] == [
        x["dataset_page_id"] for x in expected
    ]
    assert result["random_slot_marginal_inclusion_probability"] == 3 / 30
    assert len({x["dataset_page_id"] for x in result["selected"]}) == 10


# Check that random is independent of failure scores and style coverage.
def test_random_is_independent_of_failure_scores_and_style_coverage():
    pool = [window(i) for i in range(30)]
    before = module.select_batch(pool)["selected"][:3]
    for row in pool:
        row["difficulty_score"] = 100 - row["difficulty_score"]
        row["source_inventory_style_windows"] = 100 + row["source_inventory_style_windows"]
        row["adjudicated_style_windows"] = 99
    after = module.select_batch(pool)["selected"][:3]
    assert [x["dataset_page_id"] for x in before] == [x["dataset_page_id"] for x in after]


# Check that input order does not change selection.
def test_input_order_does_not_change_selection():
    pool = [window(i) for i in range(30)]
    assert module.select_batch(pool) == module.select_batch(list(reversed(pool)))


# Check that overlapping source windows are grouped transitively.
def test_overlapping_source_windows_are_grouped_transitively():
    pool = [
        window(0, source_pdf_checksum="same", context_physical_pages=[1, 2, 3]),
        window(1, source_pdf_checksum="same", context_physical_pages=[3, 4, 5]),
        window(2, source_pdf_checksum="same", context_physical_pages=[5, 6, 7]),
        window(3),
    ]
    representatives, groups = module.overlap_representatives(pool)
    assert [x["dataset_page_id"] for x in representatives] == ["page-00", "page-03"]
    assert groups["page-00"] == ["page-00", "page-01", "page-02"]
    result = module.select_batch(pool)
    assert result["sampling_units"] == 2
    assert result["selected_windows"] == 2 and result["unfilled_windows"] == 8


# Check that completed and source insufficient windows stay out of draw.
def test_completed_and_source_insufficient_windows_stay_out_of_draw():
    pool = [window(i) for i in range(20)]
    pool[0]["annotation_state"] = "ADJUDICATED_TARGETS_AVAILABLE"
    pool[1]["annotation_state"] = "SOURCE_PREPARATION_REQUIRED"
    result = module.select_batch(pool)
    assert {"page-00", "page-01"}.isdisjoint(result["random_sampling_frame"])


# Give an underrepresented catalog style priority over the broader inventory signal.
def test_rare_target_style_prioritized_before_inventory_proxy():
    # No failure stream candidates; inspect the style stream after the reserved random draw.
    pool = [
        window(
            i, difficulty_score=0, source_inventory_style_windows=0, adjudicated_style_windows=10
        )
        for i in range(30)
    ]
    random_ids = {x["dataset_page_id"] for x in module.select_batch(pool)["selected"][:3]}
    rare = next(x for x in pool if x["dataset_page_id"] not in random_ids)
    rare["adjudicated_style_windows"] = 0
    rare["source_inventory_style_windows"] = 100
    result = module.select_batch(pool)
    styles = [x for x in result["selected"] if x["selection_stream"] == "STYLE_COVERAGE"]
    assert styles[0]["dataset_page_id"] == rare["dataset_page_id"]


# Check that targeted family cap does not condition random draw.
def test_targeted_family_cap_does_not_condition_random_draw():
    pool = [window(i, family_id="same") for i in range(30)]
    result = module.select_batch(pool, family_cap=1)
    assert (
        sum(x["selection_stream"] in {"FAILURES", "STYLE_COVERAGE"} for x in result["selected"])
        == 1
    )
    assert result["actual_stream_counts"]["RANDOM"] == 3
    assert result["actual_stream_counts"]["FALLBACK_RANDOM"] == 6
    assert sum(result["targeted_shortfalls_before_backfill"].values()) == 6


# Check that missing failures are reported and backfilled explicitly.
def test_missing_failures_are_reported_and_backfilled_explicitly():
    pool = [window(i, difficulty_score=0) for i in range(30)]
    result = module.select_batch(pool)
    assert result["targeted_shortfalls_before_backfill"]["FAILURES"] == 4
    assert result["actual_stream_counts"]["FALLBACK_RANDOM"] == 4
    assert "FAILURES" not in result["actual_stream_counts"]


# Reject annotation shares that cannot form a valid sampling mixture.
@pytest.mark.parametrize(
    "budget,shares",
    [(2, (40, 30, 30)), (10, (40, 30, 31)), (10, (70, 30, 0)), (True, (40, 30, 30))],
)
def test_invalid_mixture_refused(budget, shares):
    with pytest.raises(ValueError):
        module.select_batch([], budget=budget, shares=shares)


# Check that integer budget rounding preserves every stream.
@pytest.mark.parametrize("budget", [3, 4, 5, 6, 10, 11, 20, 25])
def test_integer_budget_rounding_preserves_every_stream(budget):
    result = module.quotas(budget, (40, 30, 30))
    assert sum(result.values()) == budget
    assert all(x >= 1 for x in result.values())


# Check that empty pool has no invented sampling probability.
def test_empty_pool_has_no_invented_sampling_probability():
    result = module.select_batch([])
    assert result["selected_windows"] == 0
    assert result["random_slot_marginal_inclusion_probability"] is None


# Check that diagnostic and protected roles cannot enter annotation draw.
@pytest.mark.parametrize(
    "partition", ["VALIDATION", "EXPOSED_DIAGNOSTIC", "FRESH_VALIDATION", "TEST"]
)
def test_diagnostic_and_protected_roles_cannot_enter_annotation_draw(partition):
    with pytest.raises(ValueError, match="TUNING"):
        module.select_batch([window(0, learning_partition=partition)])


# Check that explicit exposure and duplicates cannot hide in tuning pool.
def test_explicit_exposure_and_duplicates_cannot_hide_in_tuning_pool():
    with pytest.raises(ValueError, match="TUNING"):
        module.select_batch([window(0, evaluation_role="EXPOSED_DIAGNOSTIC")])
    with pytest.raises(ValueError, match="duplicate"):
        module.select_batch([window(0), window(0)])


# Check that source cues remain questions without target labels.
def test_source_cues_remain_questions_without_target_labels():
    item = {
        "dataset_page_id": "a",
        "learning_partition": "TUNING",
        "learning_group": "group-a",
        "source_pdf_checksum": "a" * 64,
        "central_physical_page": 2,
        "context_physical_pages": [1, 2, 3],
    }
    units = [
        {"node_id": str(i), "physical_page": i, "raw_text": "text\ufffd", "evidence": []}
        for i in (1, 2, 3)
    ]
    template = {"source_pdf_checksum": "a" * 64, "units": units}
    proposal = {
        "candidates": [
            {"physical_page": 2, "candidate_id": "c", "identifier_kind": "HISTORICAL_NUMERIC"}
        ],
        "decisions": [
            {"candidate_id": "c", "context_kind": "REFERENCE_CONTAINER", "status": "UNRESOLVED"}
        ],
        "layouts": [
            {
                "physical_page": 2,
                "regions": [
                    {"role": "TABLE_CELL", "column_id": "left"},
                    {"role": "LINE", "column_id": "right"},
                ],
                "warnings": [],
            }
        ],
        "blocks": [{"candidate_id": "c", "physical_pages": [2, 3], "raw_text": "long source text"}],
        "field_sequence_proposals": [
            {"candidate_id": "c", "selected_roles": ["TITLE", "DESCRIPTION"]}
        ],
    }
    row = module.window_features(
        item, proposal, template, {"institution": "fixture", "status": "PENDING_SOURCE_REVIEW"}
    )
    assert row["annotation_state"] == "UNLABELLED_SOURCE_READY"
    assert row["difficulty_score"] == 1
    assert set(row["challenge_tags"]) == {
        "HISTORICAL_IDENTIFIER_PROPOSAL",
        "TABLE_LAYOUT_PROPOSAL",
        "MULTICOLUMN_LAYOUT_PROPOSAL",
        "CROSS_PAGE_BLOCK_PROPOSAL",
        "DAMAGED_TEXT_QUESTION",
        "NO_CREDIT_ROLE_PROPOSED",
        "REFERENCE_OR_NONCOURSE_CONTEXT_PROPOSAL",
    }
    assert not row["labels_supplied"]
    assert "label" not in row and "NO_CREDIT_COURSE" not in row["challenge_tags"]
    template["units"] = units[:2]
    assert (
        module.window_features(
            item, proposal, template, {"institution": "fixture", "status": "PENDING_SOURCE_REVIEW"}
        )["annotation_state"]
        == "SOURCE_PREPARATION_REQUIRED"
    )


# Reject invalid page metadata before opening cached labels or source files.
def test_invalid_metadata_precedes_cached_labels_and_source_reads(tmp_path, monkeypatch):
    metadata = [
        {
            "dataset_page_id": "a",
            "split": "TEST",
            "learning_partition": "TUNING",
            "learning_group": "g",
            "institution_id": "i",
            "source_pdf_checksum": "a" * 64,
            "central_physical_page": 2,
            "context_physical_pages": [1, 2, 3],
        }
    ]
    monkeypatch.setattr(module, "load_exposed_split_metadata", lambda *a: metadata)
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("source/cache opened"))
    with pytest.raises(ValueError, match="protected page"):
        module.prepare_annotation_batch(
            tmp_path / "pilot", tmp_path / "eval", tmp_path / "templates", tmp_path / "out"
        )


# Reject reserved or unregistered inputs before reading their contents.
def test_reserved_and_unregistered_arguments_precede_any_source_read(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("source/cache opened"))
    with pytest.raises(ValueError, match="registered"):
        module.prepare_annotation_batch(
            tmp_path / "pilot", tmp_path / "eval", tmp_path / "templates", tmp_path / "out"
        )
    forbidden = (
        Path(__file__).resolve().parents[1]
        / "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )
    with pytest.raises(ValueError, match="reserved cohort access"):
        module.prepare_annotation_batch(
            tmp_path / "pilot", tmp_path / "eval", forbidden, tmp_path / "out"
        )


# Make the annotation budget, random seed and sampling shares available through the command
# line.
def test_cli_exposes_budget_seed_and_three_source_shares(tmp_path, monkeypatch):
    from course_parser import cli

    calls = []
    monkeypatch.setattr(
        module,
        "prepare_annotation_batch",
        lambda *a, **kw: calls.append(kw) or {"status": "FIXTURE_ONLY"},
    )
    assert (
        cli.main(
            [
                "prepare-annotation-batch",
                "--pilot",
                str(tmp_path),
                "--evaluation",
                str(tmp_path),
                "--templates",
                str(tmp_path),
                "--output",
                str(tmp_path / "out"),
                "--budget",
                "20",
                "--shares",
                "50",
                "20",
                "30",
                "--seed",
                "9",
            ]
        )
        == 0
    )
    assert calls[0]["shares"] == (50, 20, 30) and calls[0]["budget"] == 20 and calls[0]["seed"] == 9


# Verify the sample fingerprint before reading page metadata for review images.
def test_image_preparation_rechecks_pilot_before_metadata_read(tmp_path, monkeypatch):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    (pilot / "pilot_manifest.json").write_text("{}")
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("changed pilot opened"))
    with pytest.raises(ValueError, match="pilot changed"):
        module._viewer(tmp_path / "out", {"selection": {"selected": []}}, pilot, "0" * 64)


# Verify page metadata before decoding the corresponding image.
def test_image_preparation_rechecks_metadata_before_decode(tmp_path, monkeypatch):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    manifest = pilot / "pilot_manifest.json"
    manifest.write_text(json.dumps({"file_sha256": {"selected_pages.jsonl": "0" * 64}}))
    (pilot / "selected_pages.jsonl").write_text("changed metadata")
    monkeypatch.setattr(module, "read_jsonl", lambda *a: pytest.fail("changed metadata decoded"))
    with pytest.raises(ValueError, match="metadata changed"):
        module._viewer(
            tmp_path / "out", {"selection": {"selected": []}}, pilot, module.sha256(manifest)
        )
