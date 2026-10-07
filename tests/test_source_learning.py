# Tests for development sample preparation, source-only predictions and experimental entry
# ranking.
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy

import fitz
import pytest
from fixtures import create_fixture

from course_parser.entry_learning import fit_ranker, probability, train_entry_ranker
from course_parser.io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    read_selected_jsonl,
    sha256,
    write_json,
    write_jsonl,
)
from course_parser.learning import (
    assign_learning_partitions,
    balanced_sample,
    run_learning_baseline,
    verify_learning_pilot,
)
from course_parser.learning_evaluation import (
    align_headers,
    field_values_agree,
    report_review_provenance,
    score_learning,
)
from course_parser.learning_layout import prose_column_groups


# Check that report reviewer aliases preserve raw provenance.
def test_report_reviewer_aliases_preserve_raw_provenance():
    original = {
        "reviewer_id": "primary-reviewer",
        "second_reviewer_id": "independent-reviewer",
        "adjudicator_id": None,
        "independently_recertified": False,
        "courses": [{"reviewer_id": "primary-reviewer", "description": "Printed text."}],
    }
    saved = deepcopy(original)
    display = report_review_provenance(original)
    assert original == saved
    assert display == report_review_provenance(deepcopy(original))
    assert display["reviewer_id"].startswith("reviewer-")
    assert display["reviewer_id"] != display["second_reviewer_id"]
    assert display["courses"][0]["reviewer_id"] == display["reviewer_id"]
    assert "primary-reviewer" not in json.dumps(display)
    assert "independent-reviewer" not in json.dumps(display)
    assert display["adjudicator_id"] is None
    assert display["independently_recertified"] is False
    assert display["courses"][0]["description"] == original["courses"][0]["description"]


# Check that family sampling and partitioning are seeded.
def test_family_sampling_and_partitioning_are_seeded():
    pages = [{"dataset_page_id": f"p{i}", "learning_group": f"g{i // 4}"} for i in range(20)]
    sample = balanced_sample(pages, 10, "seed")
    assert sample == balanced_sample(list(reversed(pages)), 10, "seed")
    assert len({x["learning_group"] for x in sample[:5]}) == 5
    assignments = assign_learning_partitions(pages, "seed")
    assert set(assignments.values()) == {"TUNING", "VALIDATION"}
    assert all(
        assignments[x["learning_group"]] == assignments[y["learning_group"]]
        for x in pages
        for y in pages
        if x["learning_group"] == y["learning_group"]
    )
    assert all(
        assign_learning_partitions(pages + [{"learning_group": "new-family"}], "seed")[g] == part
        for g, part in assignments.items()
    )


# Check that alignment never guesses between duplicate headers.
def test_alignment_never_guesses_between_duplicate_headers():
    candidates = [
        {
            "candidate_id": "c1",
            "physical_page": 1,
            "code_raw": "GEOL 101",
            "code_node_ids": ["n"],
            "evidence": [],
        }
    ]
    gold = [
        {
            "gold_course_id": name,
            "header_physical_page": 1,
            "code": {"raw": "GEOL101", "evidence": []},
        }
        for name in ("g1", "g2")
    ]
    assert align_headers(candidates, gold) == ({}, {"c1"})
    gold[1]["header_physical_page"] = 2
    assert align_headers(candidates, gold) == ({"c1": "g1"}, set())
    candidates[0]["code_raw"] = "GE0L 101"
    assert align_headers(candidates, gold) == ({}, set())


# Check that empty parser field is not a recovered source absence.
def test_empty_parser_field_is_not_a_recovered_source_absence():
    absent = {"raw": None, "status": "ABSENT_IN_SOURCE"}
    assert not field_values_agree({"raw": None, "status": "NOT_RECOVERED"}, absent, "credits")
    assert not field_values_agree({"raw": None, "status": "WITHHELD"}, absent, "description")
    assert field_values_agree(absent, absent, "credits")


# Check that shared answer snapshot is gated before json decoding.
def test_shared_answer_snapshot_is_gated_before_json_decoding(tmp_path):
    path = tmp_path / "answers.jsonl"
    # The excluded payload is deliberately invalid JSON. Selecting our page must never
    # deserialize it; the shared file may contain protected cohorts.
    path.write_bytes(
        b'{"dataset_page_id":"locked-page", "unopened": BROKEN}\n'
        b'{"dataset_page_id":"our-page", "answer":"selected"}\n'
    )
    assert read_selected_jsonl(path, {"our-page"}) == [
        {"dataset_page_id": "our-page", "answer": "selected"}
    ]


# Build a small development learning pilot from the synthetic catalog fixture.
def _pilot_fixture(tmp_path, reference):
    source = create_fixture(tmp_path / "source", reference)
    pilot = tmp_path / "pilot"
    packet = pilot / "packets/test-page"
    packet.mkdir(parents=True)
    script = reference.root / "scripts/course_extraction_v3_phase2/select_and_prepare_pages.py"
    spec = importlib.util.spec_from_file_location("test_learning_geometry", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = {
        "dataset_page_id": "test-page",
        "dataset_document_id": "test-document",
        "split": "DEVELOPMENT",
        "source_document_id": "doc_demo",
        "source_pdf_checksum": sha256(source.pdf),
        "source_pdf_path": str(source.pdf),
        "page_stream_path": str(source.stream),
        "central_physical_page": 2,
        "context_physical_pages": [1, 2, 3],
        "institution_id": "synthetic",
        "institution": "Synthetic University",
        "catalog_year": 1950,
        "learning_group": "synthetic",
        "learning_partition": "TUNING",
        "pilot_role": "KEYWORD_HIT",
        "source_class": "TEXT_BORN",
        "era": "MID_CENTURY_1950_1979",
        "packet_path": "packets/test-page",
        "original_page_task_id": "task_p2",
        "original_hit_count": 4,
    }
    assets = []
    with fitz.open(source.pdf) as pdf:
        for page in pdf:
            path = packet / f"p{page.number + 1}.embedded_geometry.json"
            write_json(path, module.embedded_geometry(page))
            image = packet / f"p{page.number + 1}.png"
            page.get_pixmap(dpi=100, alpha=False).save(image)
            text = packet / f"p{page.number + 1}.source_text.json"
            write_json(text, {"raw_text": page.get_text("text"), "page_number": page.number + 1})
            assets.append(
                {
                    "physical_page": page.number + 1,
                    "image_path": str(image),
                    "image_sha256": sha256(image),
                    "source_text_path": str(text),
                    "source_text_sha256": sha256(text),
                    "embedded_geometry_path": str(path),
                    "embedded_geometry_sha256": sha256(path),
                }
            )
    write_json(
        packet / "source_reference.json", {"selected_page": selected, "page_artifacts": assets}
    )
    write_jsonl(pilot / "selected_pages.jsonl", [selected])
    hits = [
        {"dataset_page_id": "test-page", "original_hit": x}
        for x in source.hits
        if x["physical_page"] == "2"
    ]
    write_jsonl(pilot / "original_hits.jsonl", hits)
    review_path, truth_path = tmp_path / "reviews.jsonl", tmp_path / "truth.jsonl"
    write_jsonl(review_path, [])
    write_jsonl(truth_path, [])
    manifest = {
        "schema_version": "course-parser-learning-pilot-v1",
        "split": "DEVELOPMENT",
        "protected_evaluation_opened": False,
        "truth_imported": False,
        "source_inputs": {
            str(source.pdf): sha256(source.pdf),
            str(source.stream): sha256(source.stream),
        },
        "metadata_inputs": {},
        "original_physical_hits": len(hits),
        "vocabulary_path": str(source.vocabulary),
        "vocabulary_sha256": sha256(source.vocabulary),
        "reference_code_sha256": reference.provenance(),
        "snapshot_paths": {
            "page_reviews.jsonl": str(review_path),
            "course_truth.jsonl": str(truth_path),
        },
        "file_sha256": artifact_hashes(pilot),
    }
    write_json(pilot / "pilot_manifest.json", manifest)
    return pilot, selected, review_path, truth_path


# Check that baseline is sealed before truth and pending is not negative.
def test_baseline_is_sealed_before_truth_and_pending_is_not_negative(tmp_path, reference):
    pilot, selected, _, _ = _pilot_fixture(tmp_path, reference)
    baseline = tmp_path / "baseline"
    result = run_learning_baseline(reference, pilot, baseline)
    assert result["failed_windows"] == 0
    assert read_json(baseline / "prediction_manifest.json")["source_truth_read"] is False
    records = read_jsonl(baseline / "course_proposals.jsonl")
    assert all(
        match["field"] in {"TITLE", "DESCRIPTION"}
        for row in records
        for match in row["keyword_matches"]
    )
    evaluation = tmp_path / "evaluation"
    result = score_learning(reference, pilot, baseline, evaluation)
    assert result["scored_pages"] == 0 and result["pending_pages"] == 1
    assert result["overall"]["course_recall"] is None
    assert read_jsonl(evaluation / "entry_training_examples.jsonl") == []
    # Hashes alone do not authorize a protected split or crossed institution.
    selected["split"] = "TEST"
    (pilot / "selected_pages.jsonl").write_text(json.dumps(selected) + "\n")
    manifest = read_json(pilot / "pilot_manifest.json")
    manifest["file_sha256"]["selected_pages.jsonl"] = sha256(pilot / "selected_pages.jsonl")
    (pilot / "pilot_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="protected page"):
        verify_learning_pilot(pilot)


# Check that missing course counts even with zero course proposals.
@pytest.mark.parametrize("proposal_status", ["PROPOSED", "PARSER_STAGE_FAILED"])
def test_missing_course_counts_even_with_zero_course_proposals(
    tmp_path, reference, proposal_status
):
    pilot, selected, review_path, truth_path = _pilot_fixture(tmp_path, reference)
    baseline = tmp_path / "baseline"
    run_learning_baseline(reference, pilot, baseline)
    # Use a sealed empty proposal result to exercise whole-page recall rather than conditional
    # field accuracy on whatever the parser happened to find.
    predictions = read_jsonl(baseline / "page_proposals.jsonl")
    predictions[0].update(status=proposal_status, candidates=[], decisions=[], blocks=[])
    (baseline / "page_proposals.jsonl").write_text(json.dumps(predictions[0]) + "\n")
    (baseline / "course_proposals.jsonl").write_text("")
    manifest = read_json(baseline / "prediction_manifest.json")
    manifest["file_sha256"] = artifact_hashes(baseline)
    manifest["file_sha256"].pop("prediction_manifest.json")
    (baseline / "prediction_manifest.json").write_text(json.dumps(manifest))
    anchor = {
        "source_document_id": "doc_demo",
        "source_pdf_checksum": selected["source_pdf_checksum"],
        "physical_page": 2,
        "representation_id": "manual:2",
        "observed_text": "GEOL 420",
        "extraction_method": "MANUAL_SOURCE_REVIEW",
        "geometry": {
            "coordinate_system": "PDF_POINTS_CANONICAL",
            "x0": 55,
            "y0": 100,
            "x1": 180,
            "y1": 120,
        },
    }
    unknown = {
        "raw": None,
        "normalized": None,
        "status": "NOT_RECOVERED",
        "evidence": [],
        "uncertainty_reason": "Not reviewed in this test",
    }
    code = {
        "raw": "GEOL 420",
        "normalized": "GEOL 420",
        "status": "OBSERVED_EXACT",
        "evidence": [anchor],
    }
    course = {
        "gold_course_id": "g1",
        "dataset_page_id": "test-page",
        "scope": "HEADER_STARTS_ON_CENTRAL_PAGE",
        "header_physical_page": 2,
        "physical_pages": [2],
        "code": code,
        "title": unknown,
        "description": unknown,
        "credits": unknown,
        "prerequisites": unknown,
        "department": unknown,
        "section_context": unknown,
        "description_status": "DESCRIPTION_NOT_RECOVERED",
        "boundary_status": "UNRESOLVED_BOUNDARY",
        "continuation_status": "NONE",
    }
    review = {
        "dataset_page_id": "test-page",
        "status": "SOURCE_REVIEW_COMPLETE_WITH_UNRESOLVED_FIELDS",
        "complete_course_inventory": True,
        "source_visible_course_count": 1,
        "gold_course_ids": ["g1"],
        "reviewer_id": "test-source-reviewer",
        "source_channels_reviewed": ["RENDERED_SOURCE_IMAGES"],
    }
    review_path.write_text(json.dumps(review) + "\n")
    truth_path.write_text(json.dumps(course) + "\n")
    result = score_learning(reference, pilot, baseline, tmp_path / "evaluation")
    assert result["overall"]["gold_courses"] == 1
    assert result["overall"]["missed_courses"] == 1
    expected_error = "MISSED_HEADER" if proposal_status == "PROPOSED" else "PARSER_STAGE_FAILED"
    assert result["error_counts"] == {expected_error: 1}
    # Source fields cannot silently move to another PDF even after rehashing.
    course["code"]["evidence"][0]["source_pdf_checksum"] = "0" * 64
    truth_path.write_text(json.dumps(course) + "\n")
    with pytest.raises(ValueError, match="another PDF"):
        score_learning(reference, pilot, baseline, tmp_path / "bad-evaluation")
    assert not (tmp_path / "bad-evaluation").exists()


# Build synthetic labelled entry examples grouped by their development source family.
def _examples():
    return [
        {
            "candidate_id": f"c{i}",
            "dataset_page_id": f"p{i}",
            "learning_partition": "TUNING",
            "learning_group": f"g{i // 4}",
            "label": i % 2,
            "label_basis": "SOURCE_REVIEWED_HEADER_ALIGNMENT"
            if i % 2
            else "COMPLETE_SOURCE_INVENTORY_NO_HEADER_AT_CANDIDATE",
            "features": {"bias": 1.0, "detail_count": float(i % 2)},
            "baseline_status": "UNRESOLVED",
        }
        for i in range(12)
    ]


# Do not train the ranker on validation examples or on a sample containing only one class.
def test_ranker_refuses_validation_or_single_class_fitting():
    rows = _examples()
    weights = fit_ranker(rows)
    assert probability(rows[1]["features"], weights) > probability(rows[0]["features"], weights)
    rows[0]["learning_partition"] = "VALIDATION"
    with pytest.raises(ValueError, match="TUNING"):
        fit_ranker(rows)
    with pytest.raises(ValueError, match="both"):
        fit_ranker(_examples()[::2])


# Check that validation labels cannot change learned weights.
def test_validation_labels_cannot_change_learned_weights(tmp_path):
    rows = _examples()
    validation = {
        **rows[0],
        "candidate_id": "v1",
        "learning_partition": "VALIDATION",
        "learning_group": "held-out",
    }
    outputs = []
    for index, label in enumerate((0, 1)):
        evaluation = tmp_path / f"evaluation-{index}"
        evaluation.mkdir()
        write_jsonl(
            evaluation / "entry_training_examples.jsonl", rows + [{**validation, "label": label}]
        )
        write_json(
            evaluation / "evaluation_manifest.json",
            {"protected_evaluation_opened": False, "file_sha256": artifact_hashes(evaluation)},
        )
        output = tmp_path / f"model-{index}"
        train_entry_ranker(evaluation, output)
        outputs.append(read_json(output / "entry_ranker.json")["weights"])
    assert outputs[0] == outputs[1]


# Check that compact tables are not repaired as parallel prose.
def test_compact_tables_are_not_repaired_as_parallel_prose():
    regions = []
    for column in range(3):
        for row in range(20):
            regions.append(
                {
                    "region_id": f"c{column}r{row}",
                    "role": "TABLE_CELL",
                    "raw_text": "Code 101" if column == 0 else "Title" if column == 1 else "3",
                    "geometry": {
                        "x0": column * 180 + 10,
                        "x1": column * 180 + 100,
                        "y0": 50 + row * 15,
                        "y1": 60 + row * 15,
                    },
                }
            )
    assert prose_column_groups(regions, 600, 800) == []
    for region in regions:
        region["raw_text"] = "A substantive line of independent column prose."
    groups = prose_column_groups(regions, 600, 800)
    assert len(groups) == 3
    assert {x["region_id"] for group in groups for x in group} == {x["region_id"] for x in regions}
