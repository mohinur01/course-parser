# Tests for separate source readings and blank review packets for possible missed courses.
"""Sealed, blank source-review inbox integration; answers never opened."""

import json

import pytest
from test_source_learning import _pilot_fixture

from course_parser.io import read_json, read_jsonl, sha256
from course_parser.learning import run_learning_baseline
from course_parser.omission_review import (
    finalize_omission_review,
    prepare_omission_review,
    verify_omission_review,
)


# Run the test pilot and remove header proposals to simulate missed course entries.
def predictions_without_headers(tmp_path, reference):
    pilot, _, reviews, truth = _pilot_fixture(tmp_path, reference)
    predictions = tmp_path / "predictions"
    run_learning_baseline(reference, pilot, predictions)
    path = predictions / "page_proposals.jsonl"
    proposal = read_jsonl(path)[0]
    proposal.update(candidates=[], blocks=[], decisions=[])
    path.write_text(json.dumps(proposal) + "\n")
    manifest_path = predictions / "prediction_manifest.json"
    manifest = read_json(manifest_path)
    manifest["file_sha256"][path.name] = sha256(path)
    manifest_path.write_text(json.dumps(manifest))
    # Deliberately invalid answer files: the audit must not deserialize them.
    reviews.write_text("UNOPENED INVALID ANSWERS")
    truth.write_text("UNOPENED INVALID ANSWERS")
    return pilot, predictions


# Keep the source-only audit, separate text readings and unanswered review queue distinct.
def test_source_only_audit_separate_extractions_and_blank_review_queue(
    tmp_path, reference, monkeypatch
):
    pilot, predictions = predictions_without_headers(tmp_path, reference)
    run = tmp_path / "audit"
    prepared = prepare_omission_review(
        pilot, predictions, run, crop_limit=2, tesseract="no-such-ocr"
    )
    assert prepared["flags"] >= 1
    assert prepared["crop_failures"] == 0
    monkeypatch.chdir(tmp_path)
    result = finalize_omission_review(run.relative_to(tmp_path))
    assert result["accepted_courses_added"] == 0
    # Readable native text and a missing proposal alone do not request OCR.
    assert prepared["ocr_eligible_crops"] == 0
    assert result["review_event_counts"].get("EXTRACTION_FAILED", 0) == 0
    assert all(not list(p.glob("tesseract_*")) for p in (run / "crops").iterdir())
    queue = read_jsonl(run / "review_queue.jsonl")
    assert all(t["disposition"] is None and t["reviewer"] is None for t in queue)
    assert all(t["course_truth_assigned"] is False for t in queue)
    raw = read_jsonl(run / "omission_flags.jsonl")
    groups = read_jsonl(run / "omission_review_groups.jsonl")
    assert sorted(f["flag_id"] for f in raw) == sorted(
        fid for group in groups for fid in group["member_flag_ids"]
    )
    assert len(queue) == len(groups)
    assert all(t["flag"]["score_is_probability"] is False for t in queue)
    assert "Review priority:" in next((run / "tasks").glob("*.html")).read_text()
    assert verify_omission_review(run)["status"] == "VERIFIED"
    assert (run / "index.html").is_file()
    with pytest.raises(FileExistsError):
        finalize_omission_review(run)
    (run / "review_queue.jsonl").write_text("altered decisions")
    with pytest.raises(ValueError, match="checksum changed"):
        verify_omission_review(run)


# Reject predictions for the wrong sample before preparing source pages.
def test_wrong_cohort_prediction_manifest_rejected_before_materialization(tmp_path, reference):
    pilot, predictions = predictions_without_headers(tmp_path, reference)
    path = predictions / "prediction_manifest.json"
    manifest = read_json(path)
    manifest["pilot_manifest_sha256"] = "other-cohort"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="cohort changed"):
        prepare_omission_review(pilot, predictions, tmp_path / "audit")
    assert not (tmp_path / "audit").exists()


# Reject named reserved paths before looking up any input.
def test_named_reserved_paths_rejected_before_input_lookup(tmp_path, monkeypatch):
    from pathlib import Path

    import course_parser.omission_review as audit

    workspace = Path(audit.__file__).resolve().parents[2]
    reserved = workspace / "artifacts/validation-transfer-v1/heldout-review"
    monkeypatch.setattr(audit, "verify_learning_pilot", lambda p: pytest.fail("input opened"))
    with pytest.raises(ValueError, match="reserved cohort"):
        prepare_omission_review(reserved, tmp_path / "pred", tmp_path / "audit")
    with pytest.raises(ValueError, match="reserved cohort"):
        prepare_omission_review(tmp_path / "pilot", tmp_path / "pred", reserved / "new")
    with pytest.raises(ValueError, match="reserved cohort"):
        finalize_omission_review(reserved)
    with pytest.raises(ValueError, match="reserved cohort"):
        verify_omission_review(reserved)
