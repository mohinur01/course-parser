# Tests for preparing equivalent source inputs for the deferred field-classifier comparison.
"""A deferred model experiment cannot change source, coverage or cohort protections."""

import copy
from pathlib import Path

import fitz
import pytest
from test_sequence_annotations import adjudicated, template_packet

from course_parser import field_experiment as module
from course_parser.io import artifact_hashes, read_json, read_jsonl, sha256, write_json, write_jsonl
from course_parser.sequence_annotations import source_order_digest


# Build a small test input with the source identity and values required by this test group.
def packet(tmp_path):
    templates, row = template_packet(tmp_path)
    source = tmp_path / "pilot" / "packets" / "sample"
    row["source_packet_path"] = str(source)
    source.mkdir(parents=True)
    image = source / "page.png"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 200), False)
    pixmap.clear_with(255)
    pixmap.save(str(image))
    unit = row["units"][0]
    unit["node_id"] = "page:header"
    unit["evidence"][0].update(
        source_document_id="pdf",
        source_pdf_checksum="a" * 64,
        physical_page=1,
        geometry={
            "coordinate_system": "PDF_POINTS_CANONICAL",
            "x0": 10,
            "y0": 10,
            "x1": 90,
            "y1": 30,
        },
    )
    geometry = source / "geometry.json"
    write_json(
        geometry,
        {
            "physical_page": 1,
            "canonical_width_points": 100,
            "canonical_height_points": 200,
            "coordinate_system": "PDF_POINTS_NATIVE_AND_ROTATION_NORMALIZED_CANONICAL",
            "lines": [
                {
                    "line_id": "header",
                    "text": unit["raw_text"],
                    "spans": [{"text": unit["raw_text"], "size": 10, "flags": 16}],
                }
            ],
        },
    )
    selected = {
        k: row[k]
        for k in (
            "dataset_page_id",
            "source_document_id",
            "source_pdf_checksum",
            "learning_group",
            "learning_partition",
            "central_physical_page",
            "context_physical_pages",
        )
    }
    selected["split"] = "DEVELOPMENT"
    write_json(
        source / "source_reference.json",
        {
            "selected_page": selected,
            "page_artifacts": [
                {
                    "physical_page": 1,
                    "image_path": str(image),
                    "image_sha256": sha256(image),
                    "embedded_geometry_path": str(geometry),
                    "embedded_geometry_sha256": sha256(geometry),
                }
            ],
        },
    )
    (templates / "sequence_template.jsonl").unlink()
    write_jsonl(templates / "sequence_template.jsonl", [row])
    (templates / "manifest.json").unlink()
    pilot_manifest = source.parent.parent / "pilot_manifest.json"
    write_json(pilot_manifest, {"file_sha256": artifact_hashes(source.parent.parent)})
    write_json(
        templates / "manifest.json",
        {
            "file_sha256": artifact_hashes(templates),
            "pilot_manifest_sha256": sha256(pilot_manifest),
        },
    )
    reviewed = tmp_path / "selected.jsonl"
    write_jsonl(reviewed, [row])
    return templates, reviewed, row


# Check that source word offsets preserve punctuation hyphens and case.
def test_source_word_offsets_preserve_punctuation_hyphens_and_case():
    unit = {"raw_text": "  GEOL-535\nOre-forming  systems. "}
    words = module.source_words(unit)
    assert [w["raw_text"] for w in words] == ["GEOL-535", "Ore-forming", "systems."]
    assert all(
        unit["raw_text"][w["text_span"]["start"] : w["text_span"]["end"]] == w["raw_text"]
        for w in words
    )


# Check that bad source boxes are rejected not clamped.
@pytest.mark.parametrize(
    "bad",
    [
        {"x0": -1, "y0": 0, "x1": 5, "y1": 5},
        {"x0": 0, "y0": 0, "x1": 101, "y1": 5},
        {"x0": 5, "y0": 0, "x1": 5, "y1": 5},
        {"x0": float("nan"), "y0": 0, "x1": 5, "y1": 5},
    ],
)
def test_bad_source_boxes_are_rejected_not_clamped(bad):
    with pytest.raises(ValueError):
        module.normalized_box(bad, 100, 200)


# Check that geometry transform is scale invariant.
def test_geometry_transform_is_scale_invariant():
    box = {"x0": 10, "y0": 20, "x1": 40, "y1": 60}
    assert module.normalized_box(box, 100, 200) == [100, 100, 400, 300]
    assert module.normalized_box({k: v * 3 for k, v in box.items()}, 300, 600) == [
        100,
        100,
        400,
        300,
    ]


# Check that blank common inputs keep labels and models deferred.
def test_blank_common_inputs_keep_labels_and_models_deferred(tmp_path):
    templates, reviewed, row = packet(tmp_path)
    output = tmp_path / "prepared"
    result = module.prepare_field_experiment(templates, reviewed, output)
    assert result["experiment_ready"] is False
    assert result["adjudicated_windows"] == 0
    assert result["model_training_or_inference_run"] is False
    unit = read_jsonl(output / "common_source_inputs.jsonl")[0]["units"][0]
    assert unit["raw_text"] == row["units"][0]["raw_text"]
    assert unit["features"]["bold_fraction"] == 1
    assert unit["word_labels"] is None
    contract = read_json(output / "experiment_contract.json")
    assert contract["apply_ocr"] is False
    assert contract["box_granularity"] == "SOURCE_SEGMENT_REPEATED_PER_WORD"
    frame = read_jsonl(output / "page_frames.jsonl")[0]
    assert frame["canonical_to_image"] == [1, 0, 0, 1, 0, 0]


# Check that human span splits keep original input words and labels source bound.
def test_human_span_splits_keep_original_input_words_and_labels_source_bound(tmp_path):
    templates, reviewed, row = packet(tmp_path)
    gold = adjudicated(row)
    proto = gold["units"][0]
    gold["units"] = []
    for start, end, role, cue, boundary in [
        (0, 8, "CODE", "EXACT_HEADER", "SAME_COURSE"),
        (8, 9, "SEPARATOR", "NONE", "SAME_COURSE"),
        (9, 16, "TITLE", "EXACT_HEADER", "EXIT_COURSE"),
    ]:
        gold["units"].append(
            {
                **proto,
                "raw_text": proto["raw_text"][start:end],
                "text_span": {"start": start, "end": end},
                "label": role,
                "owner_start_node_id": "page:header",
                "transition_cue": cue,
                "boundary_after": boundary,
            }
        )
    assert gold["source_order_review"]["source_order_sha256"] == source_order_digest(row)
    reviewed.unlink()
    write_jsonl(reviewed, [gold])
    output = tmp_path / "prepared"
    module.prepare_field_experiment(templates, reviewed, output)
    unit = read_jsonl(output / "common_source_inputs.jsonl")[0]["units"][0]
    assert unit["raw_text"] == proto["raw_text"]
    assert unit["word_labels"] == ["CODE", "CODE", "TITLE"]


# Check that word boundary crossing gold spans is unresolved.
def test_word_boundary_crossing_gold_spans_is_unresolved():
    words = module.source_words({"raw_text": "GEOL101"})
    spans = [
        {"text_span": {"start": 0, "end": 4}, "label": "CODE"},
        {"text_span": {"start": 4, "end": 7}, "label": "TITLE"},
    ]
    assert module.project_word_labels(words, spans) == [None]


# Check that typography requires exact native source line.
def test_typography_requires_exact_native_source_line(tmp_path):
    templates, reviewed, row = packet(tmp_path)
    geometry = read_json(Path(row["source_packet_path"]) / "geometry.json")
    geometry["lines"][0]["text"] = "GEOL 101 SystemS"
    features = module.unit_features(row["units"][0], geometry)
    assert features["typography_status"] == "UNAVAILABLE"
    assert features["font_size_relative"] is None


# Build comparable experiment outputs from the same synthetic source examples.
def paired_outputs(output):
    contract = read_json(output / "experiment_contract.json")
    rows = read_jsonl(output / "common_source_inputs.jsonl")
    predictions = [
        dict(
            dataset_page_id=r["dataset_page_id"],
            node_id=u["node_id"],
            text_span=w["text_span"],
            raw_text=w["raw_text"],
            label=None,
        )
        for r in rows
        for u in r["units"]
        for w in u["words"]
    ]
    result = {}
    for arm in module.ARMS:
        result[arm] = {
            "common_inputs_sha256": contract["common_inputs_sha256"],
            "shared_assembly_code_sha256": contract["shared_assembly_code_sha256"],
            **{
                k: "b" * 64
                for k in (
                    "annotation_sha256",
                    "fold_manifest_sha256",
                    "training_budget_sha256",
                    "seed_schedule_sha256",
                    "token_windows_sha256",
                    "initial_checkpoint_sha256",
                )
            },
            "annotation_sha256": contract["source_annotations_sha256"],
            "apply_ocr": False,
            "visual_embed": arm == "LAYOUTLMV3_IMAGE",
            "predictions": copy.deepcopy(predictions),
        }
    return result


# Check that shadow proposals cannot change inputs or comparison.
@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda r: r["LAYOUTLMV3_IMAGE"]["predictions"].pop(), "omitted source words"),
        (
            lambda r: r["LAYOUTLMV3_IMAGE"]["predictions"].append(
                r["LAYOUTLMV3_IMAGE"]["predictions"][0]
            ),
            "duplicates",
        ),
        (lambda r: r["LAYOUTLMV3_IMAGE"]["predictions"][0].update(raw_text="invented"), "changes"),
        (
            lambda r: r["LAYOUTLMV3_IMAGE"]["predictions"][0].update(label="COURSE_OWNED"),
            "unsupported",
        ),
        (lambda r: r["LAYOUTLMV3_IMAGE"].update(common_inputs_sha256="wrong"), "different source"),
        (
            lambda r: r["LAYOUTLMV3_IMAGE"].update(annotation_sha256="c" * 64),
            "annotations other than",
        ),
        (lambda r: r["LAYOUTLMV3_IMAGE"].update(shared_assembly_code_sha256={}), "assembly"),
        (lambda r: r["LAYOUTLMV3_IMAGE"].update(apply_ocr=True), "fresh OCR"),
        (lambda r: r["LAYOUTLMV3_NO_IMAGE"].update(visual_embed=True), "not distinguished"),
        (lambda r: r["LAYOUTLMV3_IMAGE"].update(initial_checkpoint_sha256="c" * 64), "checkpoints"),
    ],
)
def test_shadow_proposals_cannot_change_inputs_or_comparison(tmp_path, mutation, reason):
    templates, reviewed, _ = packet(tmp_path)
    output = tmp_path / "prepared"
    module.prepare_field_experiment(templates, reviewed, output)
    runs = paired_outputs(output)
    mutation(runs)
    proposals = tmp_path / "runs.json"
    write_json(proposals, runs)
    with pytest.raises(ValueError, match=reason):
        module.audit_field_proposals(output, proposals)


# Check that complete shadow provenance is not accuracy or adoption.
def test_complete_shadow_provenance_is_not_accuracy_or_adoption(tmp_path):
    templates, reviewed, _ = packet(tmp_path)
    output = tmp_path / "prepared"
    module.prepare_field_experiment(templates, reviewed, output)
    proposals = tmp_path / "runs.json"
    write_json(proposals, paired_outputs(output))
    result = module.audit_field_proposals(output, proposals)
    assert result["source_words_per_arm"] == 3
    assert result["model_accuracy_measured"] is False
    assert result["automatic_adoption"] is False
    assert result["training_provenance_independently_verified"] is False


# Reject reserved field-experiment inputs before reading any payload.
@pytest.mark.parametrize("entrypoint", ["prepare", "audit"])
def test_reserved_field_inputs_rejected_before_any_reads(tmp_path, monkeypatch, entrypoint):
    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("reserved source read"))
    monkeypatch.setattr(
        module, "audit_sequence_reviews", lambda *a: pytest.fail("reserved review read")
    )
    with pytest.raises(ValueError, match="reserved cohort access"):
        if entrypoint == "prepare":
            module.prepare_field_experiment(forbidden, tmp_path / "selected", tmp_path / "out")
        else:
            module.audit_field_proposals(forbidden, tmp_path / "proposals")


# Reject a review-packet link that points outside its allowed folder.
def test_packet_symlink_escape_rejected(tmp_path):
    packet_root = tmp_path / "packet"
    packet_root.mkdir()
    outside = tmp_path / "outside"
    alias = packet_root / "alias"
    alias.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        module._packet_file(packet_root, str(alias))


# Reject a manifest alias that leads outside the allowed folder before opening it.
@pytest.mark.parametrize("entrypoint", ["prepare", "audit"])
def test_manifest_alias_escape_rejected_before_read(tmp_path, monkeypatch, entrypoint):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "manifest.json").symlink_to(tmp_path / "outside-manifest.json")
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("escaped manifest read"))
    monkeypatch.setattr(
        module, "audit_sequence_reviews", lambda *a: pytest.fail("escaped review read")
    )
    with pytest.raises(ValueError, match="escapes"):
        if entrypoint == "prepare":
            module.prepare_field_experiment(root, tmp_path / "selected", tmp_path / "output")
        else:
            module.audit_field_proposals(root, tmp_path / "proposals")


# Check that source frames and identity are checked before use.
@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("image", "checksum changed"),
        ("identity", "checksum changed"),
        ("missing_context", "checksum changed"),
    ],
)
def test_source_frames_and_identity_are_checked_before_use(tmp_path, mutation, reason):
    templates, reviewed, row = packet(tmp_path)
    source = Path(row["source_packet_path"])
    reference = source / "source_reference.json"
    ref = read_json(reference)
    if mutation == "image":
        (source / "page.png").write_bytes(b"corrupted")
    elif mutation == "identity":
        ref["selected_page"]["source_pdf_checksum"] = "c" * 64
    else:
        ref["page_artifacts"] = []
    reference.unlink()
    write_json(reference, ref)
    with pytest.raises(ValueError, match=reason):
        module.prepare_field_experiment(templates, reviewed, tmp_path / "prepared")


# Reject a reserved output location before auditing field proposals.
def test_reserved_field_output_rejected_before_auditing(tmp_path, monkeypatch):
    from course_parser.cli import main

    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3/field-audit.json"
    )
    monkeypatch.setattr(module, "audit_field_proposals", lambda *a: pytest.fail("audit called"))
    assert (
        main(
            [
                "audit-field-proposals",
                "--experiment",
                str(tmp_path / "experiment"),
                "--proposals",
                str(tmp_path / "proposals"),
                "--output",
                str(forbidden),
            ]
        )
        == 1
    )
