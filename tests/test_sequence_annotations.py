# Tests for blank annotation packets and completed reviews tied to unchanged source text.
"""Training labels require complete source partitions and independent review."""

import copy

import pytest

from course_parser.io import artifact_hashes, write_json, write_jsonl
from course_parser.sequence_annotations import VERSION, audit_sequence_reviews, source_order_digest


# Write a synthetic sequence-annotation packet and its frozen source spans.
def template_packet(tmp_path):
    folder = tmp_path / "templates"
    folder.mkdir()
    row = {
        "schema_version": VERSION,
        "dataset_page_id": "sample",
        "source_document_id": "pdf",
        "source_pdf_checksum": "a" * 64,
        "learning_partition": "TUNING",
        "learning_group": "family",
        "central_physical_page": 1,
        "source_packet_path": str(tmp_path / "source"),
        "context_physical_pages": [1],
        "page_orders": [{"physical_page": 1, "order_confirmed": True}],
        "review_status": "PENDING",
        "reviewer": None,
        "adjudicator": None,
        "units": [
            {
                "node_id": "header",
                "physical_page": 1,
                "text_span": {"start": 0, "end": 16},
                "raw_text": "GEOL 101 Systems",
                "evidence": [{"observed_text": "GEOL 101 Systems"}],
                "label": None,
                "owner_start_node_id": None,
                "boundary_after": None,
                "transition_cue": None,
            }
        ],
    }
    write_jsonl(folder / "sequence_template.jsonl", [row])
    write_json(folder / "manifest.json", {"file_sha256": artifact_hashes(folder)})
    return folder, row


# Save the proposed test annotations and verify them against the frozen packet.
def checked(tmp_path, template, row):
    path = tmp_path / "reviews.jsonl"
    if path.exists():
        path.unlink()
    write_jsonl(path, [row])
    return audit_sequence_reviews(template, path)


# Copy the synthetic annotations and mark their review and adjudication as complete.
def adjudicated(row):
    row = copy.deepcopy(row)
    row.update(
        review_status="ADJUDICATED",
        reviewer={"name": "First reviewer", "kind": "HUMAN"},
        adjudicator={"name": "Second reviewer", "kind": "HUMAN"},
        source_order_review={
            "status": "CONFIRMED_AS_EXPORTED",
            "source_order_sha256": source_order_digest(row),
            "reviewer": "First reviewer",
            "adjudicator": "Second reviewer",
        },
    )
    row["units"][0].update(
        label="NONCOURSE",
        owner_start_node_id=None,
        boundary_after="OUTSIDE_COURSE",
        transition_cue="NONE",
    )
    return row


# Check that blank source templates are not sequence training answers.
def test_blank_source_templates_are_not_sequence_training_answers(tmp_path):
    folder, row = template_packet(tmp_path)
    result = checked(tmp_path, folder, row)
    assert result["adjudicated_windows"] == 0
    assert result["pending_windows"] == 1
    assert result["label_counts"] == {}
    assert result["crf_experiment_ready"] is False


# Check that complete source labels still require independent sequence validation.
def test_complete_source_labels_still_require_independent_sequence_validation(tmp_path):
    folder, row = template_packet(tmp_path)
    result = checked(tmp_path, folder, adjudicated(row))
    assert result["status"] == "TUNING_ANNOTATIONS_COMPLETE"
    assert result["label_counts"] == {"NONCOURSE": 1}
    assert result["crf_experiment_ready"] is False


# Check that training import rejects source mutation missing coverage and leakage.
@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda r: r["units"][0].update(raw_text="invented text"), "changed source text"),
        (lambda r: r["units"][0]["text_span"].update(start=1), "gap, overlap"),
        (lambda r: r.update(units=[]), "omitted source"),
        (lambda r: r.update(adjudicator={"name": "First reviewer", "kind": "HUMAN"}), "distinct"),
        (
            lambda r: r.update(reviewer={"name": "Parser", "kind": "MODEL"}),
            "human source reviewers",
        ),
        (lambda r: r.update(learning_partition="VALIDATION"), "frozen source identity"),
    ],
)
def test_training_import_rejects_source_mutation_missing_coverage_and_leakage(
    tmp_path, mutation, reason
):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    mutation(row)
    with pytest.raises(ValueError, match=reason):
        checked(tmp_path, folder, row)


# Check that human can split mixed code and title without losing source chars.
def test_human_can_split_mixed_code_and_title_without_losing_source_chars(tmp_path):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    prototype = row["units"][0]
    row["units"] = []
    for start, end, role, cue, boundary in [
        (0, 8, "CODE", "EXACT_HEADER", "SAME_COURSE"),
        (8, 9, "SEPARATOR", "NONE", "SAME_COURSE"),
        (9, 16, "TITLE", "EXACT_HEADER", "EXIT_COURSE"),
    ]:
        row["units"].append(
            {
                **prototype,
                "text_span": {"start": start, "end": end},
                "raw_text": prototype["raw_text"][start:end],
                "label": role,
                "owner_start_node_id": "header",
                "transition_cue": cue,
                "boundary_after": boundary,
            }
        )
    result = checked(tmp_path, folder, row)
    assert result["course_owners"] == 1
    assert result["label_counts"] == {"CODE": 1, "SEPARATOR": 1, "TITLE": 1}
    row["units"][1]["owner_start_node_id"] = None
    with pytest.raises(ValueError, match="SAME_COURSE"):
        checked(tmp_path, folder, row)


# Check that phase11 rejects false gold and identity changes.
@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda r: r.update(source_order_review=None), "human source-order review"),
        (
            lambda r: r["source_order_review"].update(source_order_sha256="wrong"),
            "human source-order review",
        ),
        (
            lambda r: r["source_order_review"].update(reviewer="Another reviewer"),
            "human source-order review",
        ),
        (lambda r: r["units"][0].update(boundary_after="UNCERTAIN"), "uncertain boundaries"),
        (lambda r: r.update(central_physical_page=2), "frozen source identity"),
        (lambda r: r.update(source_packet_path="another-source"), "frozen source identity"),
        (lambda r: r["units"][0].update(node_id="unknown"), "unknown source node"),
        (lambda r: r["units"][0]["text_span"].update(start=False), "invalid extent"),
    ],
)
def test_phase11_rejects_false_gold_and_identity_changes(tmp_path, mutation, reason):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    mutation(row)
    with pytest.raises(ValueError, match=reason):
        checked(tmp_path, folder, row)


# Check that source insufficiency can be reviewed but never counts as training.
def test_source_insufficiency_can_be_reviewed_but_never_counts_as_training(tmp_path):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    row["units"][0]["label"] = "SOURCE_INSUFFICIENT"
    result = checked(tmp_path, folder, row)
    assert result["adjudicated_windows"] == 1
    assert result["source_insufficient_windows"] == 1
    assert result["training_eligible_windows"] == 0
    assert result["training_eligible_label_counts"] == {}
    assert result["crf_experiment_ready"] is False


# Check that parser order flag is not a substitute for human order review.
def test_parser_order_flag_is_not_a_substitute_for_human_order_review(tmp_path):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    assert row["page_orders"][0]["order_confirmed"] is True
    row.pop("source_order_review")
    with pytest.raises(ValueError, match="human source-order review"):
        checked(tmp_path, folder, row)


# Check that pending parser labels do not count as training.
def test_pending_parser_labels_do_not_count_as_training(tmp_path):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    row["review_status"] = "PENDING"
    result = checked(tmp_path, folder, row)
    assert result["training_eligible_windows"] == 0
    assert result["label_counts"] == {}


# Reject repeated identities in the saved source inventory.
@pytest.mark.parametrize("duplicate", ["window", "node"])
def test_duplicate_frozen_source_identities_fail_closed(tmp_path, duplicate):
    folder, original = template_packet(tmp_path)
    rows = [original, original] if duplicate == "window" else [original]
    if duplicate == "node":
        original["units"] *= 2
    (folder / "sequence_template.jsonl").unlink()
    write_jsonl(folder / "sequence_template.jsonl", rows)
    (folder / "manifest.json").unlink()
    write_json(folder / "manifest.json", {"file_sha256": artifact_hashes(folder)})
    with pytest.raises(ValueError, match="duplicate source"):
        checked(tmp_path, folder, original)


# Check that dangling course end is not complete gold.
@pytest.mark.parametrize("boundary", ["SAME_COURSE", "NEXT_COURSE"])
def test_dangling_course_end_is_not_complete_gold(tmp_path, boundary):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    row["units"][0].update(
        label="CODE",
        owner_start_node_id="header",
        transition_cue="EXACT_HEADER",
        boundary_after=boundary,
    )
    with pytest.raises(ValueError, match="dangling course boundary"):
        checked(tmp_path, folder, row)


# Check that closed owner cannot resume after a noncourse interruption.
def test_closed_owner_cannot_resume_after_a_noncourse_interruption(tmp_path):
    folder, original = template_packet(tmp_path)
    row = adjudicated(original)
    proto = row["units"][0]
    row["units"] = []
    for start, end, role, owner, cue, boundary in [
        (0, 8, "CODE", "header", "EXACT_HEADER", "EXIT_COURSE"),
        (8, 9, "NONCOURSE", None, "NONE", "OUTSIDE_COURSE"),
        (9, 16, "TITLE", "header", "EXACT_HEADER", "EXIT_COURSE"),
    ]:
        row["units"].append(
            {
                **proto,
                "text_span": {"start": start, "end": end},
                "raw_text": proto["raw_text"][start:end],
                "label": role,
                "owner_start_node_id": owner,
                "transition_cue": cue,
                "boundary_after": boundary,
            }
        )
    with pytest.raises(ValueError, match="cannot reopen"):
        checked(tmp_path, folder, row)


# Reject reserved sequence-annotation inputs before opening them.
@pytest.mark.parametrize("entrypoint", ["audit", "export"])
def test_reserved_sequence_inputs_rejected_before_reading(tmp_path, monkeypatch, entrypoint):
    from pathlib import Path

    from course_parser import sequence_annotations as module

    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )

    # Fail the test if a guarded training or data-loading operation runs unexpectedly.
    def unexpected(*args, **kwargs):
        pytest.fail("reserved input was read before rejection")

    monkeypatch.setattr(module, "read_json", unexpected)
    monkeypatch.setattr(module, "verify_learning_pilot", unexpected)
    with pytest.raises(ValueError, match="reserved cohort access"):
        if entrypoint == "audit":
            module.audit_sequence_reviews(forbidden, tmp_path / "reviews")
        else:
            module.export_sequence_templates(forbidden, tmp_path / "predictions", tmp_path / "out")


# Reject a symbolic link into reserved sequence data before reading its target.
def test_reserved_sequence_symlink_rejected_before_reading(tmp_path, monkeypatch):
    from pathlib import Path

    from course_parser import sequence_annotations as module

    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )
    alias = tmp_path / "alias"
    alias.symlink_to(forbidden, target_is_directory=True)
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("reserved alias read"))
    with pytest.raises(ValueError, match="reserved cohort access"):
        module.audit_sequence_reviews(alias, tmp_path / "reviews")


# Check that empty source window stays pending but cannot be adjudicated.
def test_empty_source_window_stays_pending_but_cannot_be_adjudicated(tmp_path):
    folder, original = template_packet(tmp_path)
    original["units"] = []
    (folder / "sequence_template.jsonl").unlink()
    write_jsonl(folder / "sequence_template.jsonl", [original])
    (folder / "manifest.json").unlink()
    write_json(folder / "manifest.json", {"file_sha256": artifact_hashes(folder)})
    result = checked(tmp_path, folder, original)
    assert result["windows_without_source_units"] == 1
    assert result["training_eligible_windows"] == 0
    assert result["window_readiness"][0]["reason"] == "NO_EXTRACTED_SOURCE_UNITS"
    row = copy.deepcopy(original)
    row["review_status"] = "ADJUDICATED"
    with pytest.raises(ValueError, match="no source units"):
        checked(tmp_path, folder, row)


# Reject a reserved sequence output folder before auditing annotations.
def test_reserved_sequence_output_rejected_before_auditing(tmp_path, monkeypatch):
    from pathlib import Path

    from course_parser import sequence_annotations as module
    from course_parser.cli import main

    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3/new-audit.json"
    )
    monkeypatch.setattr(
        module, "audit_sequence_reviews", lambda *a: pytest.fail("audit was called")
    )
    assert (
        main(
            [
                "audit-sequence-reviews",
                "--templates",
                str(tmp_path / "templates"),
                "--reviewed",
                str(tmp_path / "reviews"),
                "--output",
                str(forbidden),
            ]
        )
        == 1
    )


# Check that first annotation batch is small diverse and keeps a negative.
def test_first_annotation_batch_is_small_diverse_and_keeps_a_negative():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/prepare_phase11_sequences.py"
    spec = importlib.util.spec_from_file_location("phase11", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{"dataset_page_id": str(i), "learning_group": str(i), "units": [1]} for i in range(10)]
    rows.append({"dataset_page_id": "empty", "learning_group": "empty", "units": []})
    selected, negatives = module.select_initial_windows(rows, {"9": {"gold_courses": 0}}, {})
    assert len(selected) == 6
    assert len({r["learning_group"] for r in selected}) == 6
    assert negatives[0] in selected
    assert all(r["units"] for r in selected)
