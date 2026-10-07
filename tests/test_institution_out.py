# Tests for institution-family comparisons and training folds that use only permitted tuning
# examples.
"""Family fold isolation, honest denominators and development-only input gates."""

import copy
from pathlib import Path

import pytest

from course_parser import institution_out as module
from course_parser.evaluation_design import audit_split_metadata


# Build a small test input with the source identity and values required by this test group.
def page(name, partition="TUNING", **changes):
    return {
        "dataset_page_id": name,
        "split": "DEVELOPMENT",
        "learning_group": "group-" + name,
        "institution_id": "institution-" + name,
        "learning_partition": partition,
        "source_pdf_checksum": name * 64,
        "central_physical_page": 2,
        "context_physical_pages": [1, 2, 3],
        **changes,
    }


# Build a small test input with the source identity and values required by this test group.
def example(p, label):
    return {
        "dataset_page_id": p["dataset_page_id"],
        "candidate_id": "candidate-" + str(label),
        "learning_partition": p["learning_partition"],
        "learning_group": p["learning_group"],
        "label": label,
        "features": {"bias": 1.0, "title_present": float(label)},
        "baseline_status": "INDEPENDENT_COURSE" if label else "REFERENCE",
        "physical_page": 2,
        "source_pdf_checksum": p["source_pdf_checksum"],
        "reviewer_id": "fixture",
        "independently_adjudicated": False,
        "label_basis": "SOURCE_REVIEWED_HEADER_ALIGNMENT"
        if label
        else "COMPLETE_SOURCE_INVENTORY_NO_HEADER_AT_CANDIDATE",
    }


# Build a small test input with the source identity and values required by this test group.
def fixture():
    pages = [page(x) for x in "abc"] + [page("d", "VALIDATION")]
    rows = [example(p, label) for p in pages for label in (0, 1)]
    return pages, rows, audit_split_metadata(pages)["family_membership"]


# Train each fold on the other institution families and predict each tuning candidate exactly
# once.
def test_fresh_folds_train_other_families_and_predict_each_tuning_candidate_once():
    pages, rows, membership = fixture()
    frozen = copy.deepcopy(rows)
    run = module.run_entry_folds(pages, rows, membership)
    assert rows == frozen
    assert run["completed_folds"] == 3
    assert run["evaluated_examples"] == 6
    assert run["excluded_diagnostic_examples"] == 2
    assert len({(x["dataset_page_id"], x["candidate_id"]) for x in run["predictions"]}) == 6
    for fold in run["folds"]:
        assert fold["held_out_family"] not in fold["train_families"]
        assert set(fold["train_window_ids"]).isdisjoint(fold["test_window_ids"])
        assert "d" not in fold["train_window_ids"] + fold["test_window_ids"]


# Check that held out labels and feature vocabulary do not change that folds weights.
def test_held_out_labels_and_feature_vocabulary_do_not_change_that_folds_weights():
    pages, rows, membership = fixture()
    before = module.run_entry_folds(pages, rows, membership)["folds"][0]
    for row in rows:
        if row["dataset_page_id"] == "a":
            row["label"] = 1 - row["label"]
            row["features"]["signal:heldout-only"] = 100.0
        if row["dataset_page_id"] == "d":
            row["features"]["signal:diagnostic-only"] = 100.0
    after = module.run_entry_folds(pages, rows, membership)["folds"][0]
    assert before["weights"] == after["weights"]
    assert before["fit_input_sha256"] == after["fit_input_sha256"]
    assert "signal:heldout-only" not in after["weights"]
    assert "signal:diagnostic-only" not in after["weights"]


# Check that family aliases remain in one fold and input order is reproducible.
def test_family_aliases_remain_in_one_fold_and_input_order_is_reproducible():
    pages, rows, membership = fixture()
    pages[1]["institution_alias_ids"] = ["institution-a"]
    membership = audit_split_metadata(pages)["family_membership"]
    assert membership["a"] == membership["b"]
    one = module.run_entry_folds(pages, rows, membership)
    two = module.run_entry_folds(list(reversed(pages)), list(reversed(rows)), membership)
    assert one == two
    assert one["planned_folds"] == 2
    assert one["folds"][0]["test_window_ids"] == ["a", "b"]
    bad = dict(membership, b="group-b")
    with pytest.raises(ValueError, match="transitive"):
        module.run_entry_folds(pages, rows, bad)


# Skip a fold that lacks a required training class instead of inventing a score.
def test_missing_training_class_skips_without_inventing_score():
    pages, rows, membership = fixture()
    rows = [x for x in rows if x["dataset_page_id"] != "a" or x["label"] == 1]
    rows = [x for x in rows if x["dataset_page_id"] == "a" or x["label"] == 0]
    run = module.run_entry_folds(pages, rows, membership)
    skipped = run["folds"][0]
    assert skipped["status"] == "SKIPPED_TRAINING_MISSING_CLASS"
    assert "ranker" not in skipped and "weights" not in skipped
    assert run["evaluated_examples"] < run["tuning_examples"]
    assert run["macro"]["ranker"]["recall"]["defined_families"] == 0


# Check that no candidate family and all negative test family are explicit.
def test_no_candidate_family_and_all_negative_test_family_are_explicit():
    pages, rows, membership = fixture()
    rows = [x for x in rows if x["dataset_page_id"] != "a"]
    rows = [x for x in rows if x["dataset_page_id"] != "b" or x["label"] == 0]
    run = module.run_entry_folds(pages, rows, membership)
    assert run["folds"][0]["status"] == "SKIPPED_NO_REVIEWED_CANDIDATES"
    assert run["folds"][1]["ranker"]["recall"] is None
    assert run["macro"]["ranker"]["recall"]["defined_families"] == 0
    assert module.mean_defined([None, None])["value"] is None


# Build synthetic scored pages for the institution-level aggregation checks.
def metrics(p, status="SCORED_DEVELOPMENT", correct=0, denominator=0):
    return {
        "dataset_page_id": p["dataset_page_id"],
        "learning_group": p["learning_group"],
        "learning_partition": p["learning_partition"],
        "physical_page": 2,
        "institution": "Fixture " + p["dataset_page_id"],
        "status": status,
        "complete_content_correct": correct,
        "complete_content_eligible": denominator,
        "complete_all_fields_correct": 0,
        "complete_all_fields_eligible": denominator,
    }


# Check that cached metrics keep failed pages pending and zero denominators.
def test_cached_metrics_keep_failed_pages_pending_and_zero_denominators():
    pages, _, membership = fixture()
    counts = {
        "complete_content_correct": 1,
        "complete_content_eligible": 5,
        "complete_all_fields_correct": 0,
        "complete_all_fields_eligible": 5,
        "pages": 3,
        "parser_failed_pages": 1,
    }
    rows = [
        metrics(pages[0], correct=1, denominator=2),
        metrics(pages[1], "PARSER_STAGE_FAILED", denominator=3),
        metrics(pages[2]),
        metrics(pages[3], "PENDING_SOURCE_REVIEW"),
    ]
    result = module.parser_family_diagnostics(rows, membership, counts)
    assert result["pooled"]["counts"] == counts
    assert result["pooled"]["rates"]["complete_content_recall"] == 1 / 5
    assert result["macro"]["complete_content_recall"]["value"] == 0.25
    assert result["macro"]["complete_content_recall"]["defined_families"] == 2
    assert result["families"][2]["rates"]["complete_content_recall"] is None
    assert result["families"][3]["excluded_windows"] == 1
    with pytest.raises(ValueError, match="reconcile"):
        module.parser_family_diagnostics(rows, membership, dict(counts, pages=2))


# Reject cached labels whose page identity, exposure permission or recorded origin does not
# match.
@pytest.mark.parametrize(
    "mutation, error",
    [
        (lambda x: x.update(learning_group="other"), "group/partition"),
        (lambda x: x.update(source_pdf_checksum="f" * 64), "source identity"),
        (lambda x: x.update(physical_page=3), "physical page"),
        (lambda x: x.update(evaluation_role="EXPOSED_DIAGNOSTIC"), "contradicts"),
        (lambda x: x.update(label_basis="PSEUDO_LABEL"), "provenance"),
        (lambda x: x.update(reviewer_id=None), "review provenance"),
        (lambda x: x["features"].update(institution=1.0), "nonstructural"),
        (lambda x: x["features"].update(title_present=float("nan")), "numeric"),
    ],
)
def test_cached_labels_fail_closed_on_identity_exposure_and_provenance(mutation, error):
    pages, rows, _ = fixture()
    mutation(rows[0])
    with pytest.raises(ValueError, match=error):
        module.bind_records(pages, [metrics(p) for p in pages], rows)


# Reject duplicate labels and labels attached to unreviewed page selections.
def test_duplicate_labels_and_unreviewed_window_labels_fail_closed():
    pages, rows, _ = fixture()
    with pytest.raises(ValueError, match="duplicate"):
        module.bind_records(pages, [metrics(p) for p in pages], rows + [rows[0]])
    with pytest.raises(ValueError, match="source-reviewed"):
        module.bind_records(pages, [metrics(p, "PENDING_SOURCE_REVIEW") for p in pages], rows)


# Reject unregistered and reserved paths before reading input files.
def test_unregistered_and_reserved_paths_rejected_before_input_read(tmp_path, monkeypatch):
    from course_parser import evaluation_design

    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("input opened"))
    monkeypatch.setattr(evaluation_design, "read_json", lambda *a: pytest.fail("metadata opened"))
    with pytest.raises(ValueError, match="registered"):
        module.evaluate_institution_out(tmp_path / "pilot", tmp_path / "eval", tmp_path / "out")
    # Only a synthetic forbidden path is supplied. No reserved files are opened.
    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )
    with pytest.raises(ValueError, match="reserved cohort access"):
        module.evaluate_institution_out(forbidden, tmp_path / "eval", tmp_path / "out")


# Check that fold explicit role cannot hide exposure.
def test_fold_explicit_role_cannot_hide_exposure():
    pages, rows, membership = fixture()
    rows[0]["evaluation_role"] = "EXPOSED_DIAGNOSTIC"
    with pytest.raises(ValueError, match="exposure"):
        module.run_entry_folds(pages, rows, membership)


# Reject invalid metadata before reading the cached labels it claims to describe.
def test_invalid_metadata_rejected_before_cached_label_read(tmp_path, monkeypatch):
    pages, _, _ = fixture()
    pages[0]["split"] = "TEST"
    monkeypatch.setattr(module, "load_exposed_split_metadata", lambda *a: pages)
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("cached input opened"))
    with pytest.raises(ValueError, match="protected page"):
        module.evaluate_institution_out(tmp_path / "pilot", tmp_path / "eval", tmp_path / "out")


# Check the cached file fingerprint before decoding its contents.
def test_cached_file_checksum_rejected_before_deserialization(tmp_path, monkeypatch):
    from course_parser.io import write_json, write_jsonl

    pages, _, _ = fixture()
    evaluation = tmp_path / "eval"
    evaluation.mkdir()
    write_jsonl(evaluation / "page_metrics.jsonl", [metrics(p) for p in pages])
    write_json(
        evaluation / "evaluation_manifest.json",
        {
            "protected_evaluation_opened": False,
            "file_sha256": {"page_metrics.jsonl": "bad"},
        },
    )
    monkeypatch.setattr(module, "load_exposed_split_metadata", lambda *a: pages)
    monkeypatch.setattr(module, "read_jsonl", lambda *a: pytest.fail("changed cache decoded"))
    with pytest.raises(ValueError, match="checksum changed"):
        module.evaluate_institution_out(tmp_path / "pilot", evaluation, tmp_path / "out")
