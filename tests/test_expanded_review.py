# Tests for keeping random and targeted review selections separate and reproducible.
"""Retain independent random selection even when source signals change."""

from test_source_learning import _pilot_fixture

from course_parser.expanded_review import build_expanded_review
from course_parser.io import artifact_hashes, read_json, read_jsonl, write_json, write_jsonl
from course_parser.learning import run_learning_baseline


# Check that unique paths do not raise transition flags or reorder random pages.
def test_unique_paths_do_not_raise_transition_flags_or_reorder_random_pages(tmp_path, monkeypatch):
    monkeypatch.setattr("course_parser.expanded_review._verified_predictions", lambda *args: None)
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    write_json(pilot / "pilot_manifest.json", {})
    pages = []
    for pid in ("random-1", "random-2"):
        packet = pilot / pid
        packet.mkdir()
        write_json(packet / "source_reference.json", {"page_artifacts": []})
        pages.append(
            dict(
                dataset_page_id=pid,
                learning_group=pid,
                learning_partition="TUNING",
                packet_path=pid,
                central_physical_page=1,
                original_hit_count=0,
                pilot_role="RANDOM",
                selection_role="RANDOM_UNBIASED",
                cohort="PENDING_RANDOM_REVIEW",
                source_document_id=pid,
                source_pdf_checksum="a" * 64,
                original_page_task_id=None,
            )
        )
    write_jsonl(pilot / "selected_pages.jsonl", pages)
    outputs = []
    for index, status in enumerate(("UNIQUE", "AMBIGUOUS")):
        predictions = tmp_path / f"predictions-{index}"
        predictions.mkdir()
        write_json(predictions / "prediction_manifest.json", {})
        write_jsonl(
            predictions / "page_proposals.jsonl",
            [
                dict(
                    dataset_page_id=p["dataset_page_id"],
                    status="PROPOSED",
                    candidates=[],
                    field_sequence_proposals=[{"status": status}],
                )
                for p in pages
            ],
        )
        output = tmp_path / f"queue-{index}"
        build_expanded_review(pilot, predictions, output)
        outputs.append(read_jsonl(output / "review_queue.jsonl"))
    assert [p["dataset_page_id"] for p in outputs[0]] == [p["dataset_page_id"] for p in outputs[1]]
    assert all("FIELD_TRANSITION_REVIEW" not in p["signals"] for p in outputs[0])
    assert all("FIELD_TRANSITION_REVIEW" in p["signals"] for p in outputs[1])


# Check that random review is retained and answers stay unopened.
def test_random_review_is_retained_and_answers_stay_unopened(tmp_path, reference):
    pilot, selected, reviews, truth = _pilot_fixture(tmp_path, reference)
    selected.update(
        pilot_role="RANDOM", selection_role="RANDOM_UNBIASED", cohort="PENDING_RANDOM_REVIEW"
    )
    (pilot / "selected_pages.jsonl").unlink()
    write_jsonl(pilot / "selected_pages.jsonl", [selected])
    packet = pilot / selected["packet_path"]
    source = read_json(packet / "source_reference.json")
    source["selected_page"] = selected
    (packet / "source_reference.json").unlink()
    write_json(packet / "source_reference.json", source)
    manifest = read_json(pilot / "pilot_manifest.json")
    (pilot / "pilot_manifest.json").unlink()
    manifest["file_sha256"] = artifact_hashes(pilot)
    write_json(pilot / "pilot_manifest.json", manifest)
    predictions = tmp_path / "predictions"
    run_learning_baseline(reference, pilot, predictions, contextual_course_assembly=True)
    reviews.write_text("INVALID UNOPENED REVIEW ANSWERS")
    truth.write_text("INVALID UNOPENED GOLD ANSWERS")
    result = build_expanded_review(pilot, predictions, tmp_path / "queue")
    rows = read_jsonl(tmp_path / "queue/review_queue.jsonl")
    assert result["source_truth_read"] is False
    assert result["pending_random_windows"] == 1
    assert result["lane_counts"] == {"RANDOM": 1}
    assert len(rows) == 1 and rows[0]["source_review_decision"] is None
    assert rows[0]["original_hit_count"] == 4
    assert rows[0]["may_train"] is True and rows[0]["is_accuracy_sample"] is False
