# Tests for importing optional layout and OCR suggestions, including missing output and engine
# failures.
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from test_docling_concepts import evidence_fixture, model_export

from course_parser import ppstructure_runner
from course_parser.cli import main
from course_parser.io import read_json, read_jsonl, sha256, write_json, write_jsonl
from course_parser.layout_models import Box, PagePredictions, Region
from course_parser.layout_stage import prepare_layout, verify_layout
from course_parser.pdf_backend import PyMuPDFPageBackend
from course_parser.ppstructure_adapter import compare_layouts, parse_ppstructure_page


# Build the four corners of a rectangular test polygon.
def poly(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# Build a synthetic engine response containing layout regions, recognized words and detection
# scores.
def payload():
    return {
        "res": {
            "page_index": None,
            "width": 1000,
            "height": 800,
            "model_settings": {"use_doc_preprocessor": False, "use_table_recognition": True},
            "parsing_res_list": [
                {
                    "block_id": 9,
                    "block_label": "header",
                    "block_order": None,
                    "block_bbox": [10, 0, 900, 15],
                    "block_content": "Catalog",
                },
                {
                    "block_id": 3,
                    "block_label": "text",
                    "block_order": 1,
                    "block_bbox": [10, 20, 900, 200],
                    "block_content": "Ore dep0sit",
                },
            ],
            "overall_ocr_res": {
                "rec_texts": ["Ore dep0sit", "Mineral systems"],
                "rec_scores": [0.98, 0.45],
                "rec_polys": [poly(20, 30, 200, 60), poly(20, 300, 300, 330)],
                "dt_polys": [
                    poly(20, 30, 200, 60),
                    poly(20, 300, 300, 330),
                    poly(20, 400, 300, 430),
                ],
                "dt_scores": [0.6, 0.7, 0.8],
                "text_rec_score_thresh": 0.0,
            },
        }
    }


# Parse the synthetic response using the test page dimensions and source identity.
def parse(raw):
    return parse_ppstructure_page(
        raw,
        page_key="page",
        image_width=1000,
        image_height=800,
        width=500,
        height=400,
        engine_version="synthetic-contract",
    )


# Write a synthetic layout export derived from the approved test page.
def pp_export(tmp_path, evidence, *, reverse=False):
    page = next(p for p in read_jsonl(evidence / "page_evidence.jsonl") if p["physical_page"] == 2)
    image = fitz.Pixmap(str(evidence / page["image_relative_path"]))
    pdf_path = Path(read_jsonl(evidence / "task_evidence.jsonl")[0]["source_pdf_path"])
    with fitz.open(pdf_path) as pdf:
        native = PyMuPDFPageBackend().extract(pdf[1], page["page_key"])

    # Scale the original PDF box into the rendered test image coordinates.
    def scaled(box):
        return [
            box.x0 * image.width / native.width,
            box.y0 * image.height / native.height,
            box.x1 * image.width / native.width,
            box.y1 * image.height / native.height,
        ]

    blocks = [
        {
            "block_id": i,
            "block_label": "text",
            "block_order": i + 1,
            "block_content": r.predicted_text,
            "block_bbox": scaled(r.box),
        }
        for i, r in enumerate(native.regions)
    ]
    if reverse:
        blocks.reverse()
        for index, block in enumerate(blocks):
            block["block_order"] = index + 1
    polygons = [poly(*scaled(cell.box)) for cell in native.cells]
    raw = {
        "res": {
            "page_index": None,
            "width": image.width,
            "height": image.height,
            "model_settings": {"use_doc_preprocessor": False},
            "parsing_res_list": blocks,
            "overall_ocr_res": {
                "rec_texts": [c.text for c in native.cells],
                "rec_scores": [0.97] * len(native.cells),
                "rec_polys": polygons,
                "dt_polys": polygons,
                "dt_scores": [0.91] * len(native.cells),
                "text_rec_score_thresh": 0.0,
            },
        }
    }
    raw_path = tmp_path / "ppstructure.json"
    write_json(raw_path, raw)
    manifest = tmp_path / "ppstructure-input.json"
    write_json(
        manifest,
        {
            "schema_version": "course-parser-ppstructure-input-v1",
            "coordinate_frame": "VERIFIED_UPRIGHT_IMAGE",
            "input_kind": "RENDERED_PAGE_IMAGE",
            "pages": [
                {
                    "page_key": page["page_key"],
                    "source_pdf_checksum": page["source_pdf_checksum"],
                    "image_sha256": page["image_sha256"],
                    "image_width": image.width,
                    "image_height": image.height,
                    "engine_version": "synthetic-contract-fixture",
                    "result_json_path": raw_path.name,
                    "result_json_sha256": sha256(raw_path),
                }
            ],
        },
    )
    return manifest


# Update the chosen test JSON fields and save the altered fixture.
def replace_json(path, value):
    path.unlink()
    write_json(path, value)


# Keep exported list order usable when display indices are absent, and preserve OCR confidence
# separately.
def test_ordered_list_null_display_indices_and_independent_ocr_confidence():
    proposal = parse(payload())
    assert proposal.layout.reading_order == ("page:pp:block:9", "page:pp:block:3")
    assert proposal.layout.reading_order_status == "MODEL_PROPOSED"
    assert proposal.layout.regions[0].furniture
    assert proposal.ocr.raw_text == "Ore dep0sit\nMineral systems"  # never auto-correct OCR
    assert len(proposal.ocr.detections) == 3 and len(proposal.ocr.lines) == 2
    assert proposal.ocr.lines[0].recognition_score == 0.98
    assert proposal.ocr.detections[0].detection_score == 0.6
    assert proposal.ocr.lines[0].box == Box(x0=10, y0=15, x1=100, y1=30)
    assert proposal.ocr.assignments[1].status == "ORPHAN"
    assert any("LOW_RECOGNITION_SCORE" in f for f in proposal.ocr.review_flags)
    for line in proposal.ocr.lines:
        assert proposal.ocr.raw_text[line.span.start : line.span.end] == line.text


# Check that transformed or unknown coordinate frames are rejected.
@pytest.mark.parametrize("change", ["unwarp", "orientation", "pdf_input", "dimension", "unknown"])
def test_transformed_or_unknown_coordinate_frames_are_rejected(change):
    raw = payload()
    result = raw["res"]
    if change in {"unwarp", "orientation"}:
        result["model_settings"]["use_doc_preprocessor"] = True
        result["doc_preprocessor_res"] = {
            "angle": 90 if change == "orientation" else 0,
            "model_settings": {
                "use_doc_orientation_classify": change == "orientation",
                "use_doc_unwarping": change == "unwarp",
            },
        }
    elif change == "pdf_input":
        result["page_index"] = 0
    elif change == "dimension":
        result["width"] = 800
    else:
        result.pop("model_settings")
    with pytest.raises(ValueError):
        parse(raw)


# Keep contradictory order, empty OCR results and specialized extraction routes visible.
def test_contradictory_order_empty_recognition_and_specialized_routing():
    raw = payload()
    result = raw["res"]
    result["parsing_res_list"].append(
        {
            "block_id": 8,
            "block_label": "table",
            "block_order": 1,
            "block_bbox": [10, 300, 900, 600],
            "block_content": "<table><tr><td>Mineral systems</td></tr></table>",
        }
    )
    result["overall_ocr_res"]["rec_texts"][0] = ""
    proposal = parse(raw)
    assert proposal.layout.reading_order_status == "UNRESOLVED"
    assert proposal.ocr.lines[0].span is None
    assert len(proposal.ocr.lines) == 2
    assert len(proposal.ocr.assignments) == 1
    assert proposal.specialized_routes[0].module_enabled is True
    assert proposal.specialized_routes[0].route == "TABLE_STRUCTURE_REVIEW_REQUIRED"
    assert proposal.layout.tables == ()  # no unsupported physical-cell mapping


# Check that invalid ppstructure evidence is not silently dropped.
@pytest.mark.parametrize(
    "change", ["recognition_length", "detection_length", "out_of_bounds", "score", "block_id"]
)
def test_invalid_ppstructure_evidence_is_not_silently_dropped(change):
    raw = payload()
    result = raw["res"]
    if change == "recognition_length":
        result["overall_ocr_res"]["rec_scores"].pop()
    elif change == "detection_length":
        result["overall_ocr_res"]["dt_scores"].pop()
    elif change == "out_of_bounds":
        result["overall_ocr_res"]["rec_polys"][0][0][0] = -10
    elif change == "score":
        result["overall_ocr_res"]["rec_scores"][0] = float("nan")
    else:
        result["parsing_res_list"][1]["block_id"] = 9
    with pytest.raises(ValueError):
        parse(raw)


# Check that two engine conflicts stay review cues.
def test_two_engine_conflicts_stay_review_cues():
    raw = payload()
    raw["res"]["parsing_res_list"].append(
        {
            "block_id": 8,
            "block_label": "text",
            "block_order": 2,
            "block_bbox": [10, 300, 900, 600],
            "block_content": "Mineral systems",
        }
    )
    other = parse(raw).layout
    primary = PagePredictions(
        engine="DOCLING",
        engine_version="synthetic",
        regions=tuple(
            Region(
                region_id=f"doc:{i}",
                origin="DOCLING",
                label="table" if i == 1 else r.label,
                box=r.box,
                predicted_text=r.predicted_text,
                source_ref=f"texts/{i}",
                furniture=r.furniture,
            )
            for i, r in enumerate(other.regions)
        ),
        reading_order=("doc:2", "doc:1"),
        reading_order_status="MODEL_PROPOSED",
        assignments=(),
    )
    comparison = compare_layouts(primary, other)
    assert comparison["status"] == "REVIEW_REQUIRED"
    assert "ENGINE_READING_ORDER_DISAGREEMENT" in comparison["review_flags"]
    assert any("REGION_ROLE_DISAGREEMENT" in f for f in comparison["review_flags"])
    assert compare_layouts(None, other)["status"] == "NOT_COMPARABLE"


# Check that combined pipeline keeps all original hits and independent channels.
def test_combined_pipeline_keeps_all_original_hits_and_independent_channels(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    docling = model_export(tmp_path, evidence)
    pp = pp_export(tmp_path, evidence, reverse=True)
    output = tmp_path / "combined"
    result = prepare_layout(
        reference, evidence, output, docling_manifest=docling, ppstructure_manifest=pp
    )
    assert result["ppstructure_pages_imported"] == result["docling_pages_imported"] == 1
    assert result["pending_dispositions"] == 6 and result["finalized_dispositions"] == 0
    assert result["geometry_status_counts"] == {"EXACT": 6}
    assert (output / "original_hit_inventory.jsonl").read_bytes() == (
        evidence / "hit_evidence.jsonl"
    ).read_bytes()
    central = next(p for p in read_jsonl(output / "page_layout.jsonl") if p["physical_page"] == 2)
    assert central["predictions"]["engine"] == "DOCLING"
    assert central["ppstructure"]["layout"]["engine"] == "PP_STRUCTURE_V3"
    assert central["layout_comparison"]["status"] == "MODEL_AGREEMENT_PROPOSED"
    assert central["frozen_representation"]["raw_text"] != central["ppstructure"]["ocr"]["raw_text"]
    assert verify_layout(output, recheck_inputs=True)["status"] == "PASS"
    assert main(["verify-layout", "--run", str(output)]) == 0


# Check that unbound import is rejected before creating output.
@pytest.mark.parametrize("change", ["image_hash", "image_size", "json_hash", "frame", "path"])
def test_unbound_import_is_rejected_before_creating_output(tmp_path, reference, change):
    _, evidence = evidence_fixture(tmp_path, reference)
    manifest = pp_export(tmp_path, evidence)
    raw = read_json(manifest)
    entry = raw["pages"][0]
    if change == "image_hash":
        entry["image_sha256"] = "0" * 64
    elif change == "image_size":
        entry["image_width"] += 1
    elif change == "json_hash":
        entry["result_json_sha256"] = "0" * 64
    elif change == "frame":
        raw["coordinate_frame"] = "UNKNOWN"
    else:
        entry["result_json_path"] = "../outside.json"
    replace_json(manifest, raw)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        prepare_layout(reference, evidence, output, ppstructure_manifest=manifest)
    assert not output.exists()


# Check that model failure preserves native and Docling evidence.
def test_model_failure_preserves_native_and_docling_evidence(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    docling = model_export(tmp_path, evidence)
    manifest = pp_export(tmp_path, evidence)
    raw_path = tmp_path / "ppstructure.json"
    raw = read_json(raw_path)
    raw["res"]["overall_ocr_res"]["rec_scores"].pop()
    replace_json(raw_path, raw)
    binding = read_json(manifest)
    binding["pages"][0]["result_json_sha256"] = sha256(raw_path)
    replace_json(manifest, binding)
    output = tmp_path / "failed-model"
    result = prepare_layout(
        reference, evidence, output, docling_manifest=docling, ppstructure_manifest=manifest
    )
    assert result["status"] == "LAYOUT_INPUT_INCOMPLETE"
    assert result["geometry_status_counts"] == {"EXACT": 6}
    central = next(p for p in read_jsonl(output / "page_layout.jsonl") if p["physical_page"] == 2)
    assert central["predictions"]["engine"] == "DOCLING" and central["native_cells"]
    assert central["ppstructure_status"] == "INPUT_INCOMPLETE"
    assert verify_layout(output, recheck_inputs=True)["status"] == "PASS"


# Check that rehashed OCR tampering is detected against archived export.
def test_rehashed_ocr_tampering_is_detected_against_archived_export(tmp_path, reference):
    _, evidence = evidence_fixture(tmp_path, reference)
    pp = pp_export(tmp_path, evidence)
    output = tmp_path / "layout"
    prepare_layout(reference, evidence, output, ppstructure_manifest=pp)
    pages = read_jsonl(output / "page_layout.jsonl")
    central = next(p for p in pages if p["physical_page"] == 2)
    central["ppstructure"]["ocr"]["lines"][0]["recognition_score"] = 0.99
    path = output / "page_layout.jsonl"
    path.unlink()
    write_jsonl(path, pages)
    manifest_path = output / "layout_manifest.json"
    manifest = read_json(manifest_path)
    manifest["files_sha256"][path.name] = sha256(path)
    replace_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="archived model export"):
        verify_layout(output)


# Reuse the optional engine across pages and save each page failure independently.
def test_optional_runner_reuses_pipeline_and_records_per_page_failures(
    tmp_path, reference, monkeypatch
):
    _, evidence = evidence_fixture(tmp_path, reference)
    calls = []

    # Record the engine construction arguments and return the test implementation.
    def factory(device):
        calls.append(device)

        # Run the configured engine for one verified input and return its recorded output.
        def analyze(path):
            calls.append(str(path))
            # Simulate a page failure so the wrapper's failure record can be checked.
            if len(calls) == 3:
                raise RuntimeError("synthetic page failure")
            raw = payload()
            image = fitz.Pixmap(str(path))
            raw["res"].update(width=image.width, height=image.height)
            return raw

        return {"paddleocr": "synthetic-test-double", "paddlex": "synthetic-test-double"}, analyze

    monkeypatch.setattr(ppstructure_runner, "_engine", factory)
    output = tmp_path / "engine-run"
    result = ppstructure_runner.run_ppstructure(evidence, output)
    assert result["page_status_counts"] == {"EXPORTED": 2, "FAILED": 1}
    assert calls[0] == "cpu" and len(calls) == 4
    entries = read_json(output / "ppstructure-input.json")["pages"]
    assert len(entries) == 3 and sum(e["status"] == "EXPORTED" for e in entries) == 2
    assert read_json(output / "engine_run_manifest.json")["new_keyword_hits_created"] is False
    layout = tmp_path / "layout"
    imported = prepare_layout(
        reference, evidence, layout, ppstructure_manifest=output / "ppstructure-input.json"
    )
    assert imported["ppstructure_pages_imported"] == 2
    assert imported["ppstructure_page_status_counts"]["INPUT_INCOMPLETE"] == 1
    assert imported["native_cells"] == 63 and imported["pending_dispositions"] == 6
    assert verify_layout(layout, recheck_inputs=True)["status"] == "PASS"
    with pytest.raises(FileExistsError):
        ppstructure_runner.run_ppstructure(evidence, output)


# Check that optional engine configuration keeps source frame and low score text.
def test_optional_engine_configuration_keeps_source_frame_and_low_score_text(monkeypatch):
    captured = []

    # A small stand-in for the optional OCR tool, used to test its wrapper without running the
    # real engine.
    class FakePipeline:
        # Store the inputs and helpers this object uses in its later methods.
        def __init__(self, **settings):
            captured.append(settings)

        # Return the synthetic engine response for the test input.
        def predict(self, **kwargs):
            return [SimpleNamespace(json=payload())]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=FakePipeline))
    monkeypatch.setattr(ppstructure_runner, "version", lambda name: "synthetic-test-double")
    _, analyze = ppstructure_runner._engine("cpu")
    assert captured[0]["use_doc_unwarping"] is False
    assert captured[0]["use_doc_orientation_classify"] is False
    assert captured[0]["text_rec_score_thresh"] == 0.0
    assert analyze(Path("synthetic.png")) == payload()


# Check that unavailable engine leaves no partial output.
def test_unavailable_engine_leaves_no_partial_output(tmp_path, reference, monkeypatch, capsys):
    _, evidence = evidence_fixture(tmp_path, reference)

    # Simulate a missing optional engine dependency.
    def unavailable(device):
        raise RuntimeError("synthetic unavailable neural runtime")

    monkeypatch.setattr(ppstructure_runner, "_engine", unavailable)
    output = tmp_path / "unavailable"
    assert main(["run-ppstructure", "--evidence-run", str(evidence), "--output", str(output)]) == 1
    assert "unavailable neural runtime" in capsys.readouterr().err
    assert not output.exists()
