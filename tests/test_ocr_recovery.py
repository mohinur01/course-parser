# Tests for requesting OCR only for source-quality problems and retaining competing readings.
"""OCR requests need source quality evidence; competing readings cannot replace text."""

import copy
import json
import shutil
from pathlib import Path

import fitz
import pytest
from test_crop_extractions import crop, other_extraction

from course_parser.crop_extractions import render_crop, run_tesseract
from course_parser.io import artifact_hashes, read_json, sha256, write_json
from course_parser.layout_models import Box
from course_parser.ocr_recovery import reconcile_crop, route_crop, route_native_record


# Build a synthetic OCR crop record with its native evidence, geometry and engine readings.
def record(text="535 Environmental Economics", **values):
    return {"status": "EXTRACTED", "raw_text": text, "review_flags": [], "lines": [], **values}


# Check that readable native and parser failure do not trigger OCR.
@pytest.mark.parametrize(
    "kind", [None, "HEADER_WITHHELD_BY_ENTRY_STAGE", "POSSIBLE_HEADER_INSIDE_OWNED_BODY"]
)
def test_readable_native_and_parser_failure_do_not_trigger_ocr(kind):
    result = route_native_record(record(), omission_kind=kind)
    assert result["ocr_eligible"] is False
    assert result["automatic_text_replacement"] is False


# Do not guess character replacements or request OCR simply because native text is short.
@pytest.mark.parametrize("text", ["GE0L 535", "Min. geo.", "À propos — 535", "x² + y²", "535"])
def test_no_character_confusion_dictionary_or_short_text_heuristic(text):
    assert route_native_record(record(text))["decision"] == "KEEP_NATIVE"


# Request a small OCR probe when native text is missing or explicitly damaged.
@pytest.mark.parametrize("text", ["", " \n", "17. Minera\ufffdogy", "\x00Mineralogy", "\ue123"])
def test_empty_and_explicitly_damaged_native_request_bounded_probe(text):
    result = route_native_record(record(text))
    assert result["decision"] == "TARGET_OCR"
    assert result["reasons"]


# Check that native failure requests OCR and clipping requires bounds review first.
def test_native_failure_requests_ocr_and_clipping_requires_bounds_review_first():
    assert route_native_record(record(None, status="FAILED"))["ocr_eligible"] is True
    result = route_native_record(
        record("\ufffd", review_flags=["NATIVE_TEXT_INTERSECTS_CROP_EDGE"])
    )
    assert result["decision"] == "REVIEW_CROP_BOUNDS"
    assert result["ocr_eligible"] is False


# Build synthetic neighboring original text pieces that expose a cross-column merge.
def cross_column_context():
    native = record(
        lines=[
            {
                "line_id": "crossed",
                "source_cells": [
                    {"box": {"x0": 10, "y0": 10, "x1": 20, "y1": 20}},
                    {"box": {"x0": 110, "y0": 10, "x1": 120, "y1": 20}},
                ],
            }
        ]
    )
    layout = {
        "regions": [
            {
                "column_id": "left",
                "role": "LINE",
                "geometry": {"x0": 0, "y0": 0, "x1": 80, "y1": 80},
            },
            {
                "column_id": "right",
                "role": "LINE",
                "geometry": {"x0": 100, "y0": 0, "x1": 180, "y1": 80},
            },
        ]
    }
    return native, layout


# Check that source column crossing requires local crops and tables need structure review.
def test_source_column_crossing_requires_local_crops_and_tables_need_structure_review():
    native, layout = cross_column_context()
    before = copy.deepcopy((native, layout))
    result = route_native_record(native, layout=layout)
    assert result["decision"] == "REVIEW_COLUMN_CROP"
    assert result["ocr_eligible"] is False
    assert result["cross_column_lines"][0]["columns"] == ["left", "right"]
    assert (native, layout) == before
    layout["regions"][0]["role"] = "TABLE_CELL"
    assert route_native_record(native, layout=layout)["decision"] == "REVIEW_TABLE_STRUCTURE"


# Check that ambiguous column membership is not confirmed cross column merge.
def test_ambiguous_column_membership_is_not_confirmed_cross_column_merge():
    native, layout = cross_column_context()
    layout["regions"][0]["geometry"] = {"x0": 0, "y0": 0, "x1": 180, "y1": 80}
    assert route_native_record(native, layout=layout)["ocr_eligible"] is False


# Check that readable native survives two same engine modes and raws stay immutable.
def test_readable_native_survives_two_same_engine_modes_and_raws_stay_immutable(tmp_path):
    root, _, _ = crop(tmp_path)
    other_extraction(root, "tesseract_psm6", "S35 Environmental Economlcs")
    other_extraction(root, "tesseract_psm11", "S35 Environmental Economlcs\n")
    before = artifact_hashes(root)
    result = reconcile_crop(root)
    assert result["decision"] == "KEEP_NATIVE_WITH_OCR_DISAGREEMENT"
    assert result["alternative_readings"][0]["independent_engine_count"] == 1
    assert result["primary_text_source"] == "native"
    assert result["consensus_text_created"] is False
    assert artifact_hashes(root) == before


# Agreement between distinct OCR tools may suggest a correction, but still requires source
# review.
def test_two_distinct_engine_families_only_propose_correction_for_review(tmp_path):
    root, _, _ = crop(tmp_path)
    other_extraction(root, "a", "GEOL 535", engine="TESSERACT")
    other_extraction(root, "b", "GEOL 535", engine="SECOND_FIXTURE_OCR")
    result = reconcile_crop(root)
    assert result["decision"] == "REVIEW_CORROBORATED_ALTERNATIVE"
    assert result["automatic_text_replacement"] is False
    assert result["review_answer"] is None
    assert result["keyword_hit_dispositions_changed"] is False


# Check that native only is intentional no second engine error.
def test_native_only_is_intentional_no_second_engine_error(tmp_path):
    root, _, _ = crop(tmp_path)
    result = reconcile_crop(root)
    assert result["ocr_missing_by_design"] is True
    assert not any(
        e["kind"] == "SECOND_EXTRACTION_MISSING" for e in result["comparisons"]["review_events"]
    )


# Check that failed requested OCR is preserved not empty agreement.
def test_failed_requested_ocr_is_preserved_not_empty_agreement(tmp_path):
    root, _, _ = crop(tmp_path)
    run_tesseract(root, psm=6, executable="missing-ocr-engine")
    result = reconcile_crop(root)
    assert result["failed_extraction_ids"] == ["tesseract_psm6"]
    assert result["comparisons"]["pairs"][0]["status"] == "NOT_COMPARABLE"


# Check that frame identity and raw tampering are rejected.
def test_frame_identity_and_raw_tampering_are_rejected(tmp_path):
    root, frame, _ = crop(tmp_path)
    layout = {
        "source_pdf_checksum": "different",
        "physical_page": 1,
        "page_width": frame.page_width,
        "page_height": frame.page_height,
    }
    with pytest.raises(ValueError, match="coordinate frame"):
        route_crop(root, layout=layout)
    (root / "native/raw.txt").write_text("silently corrected")
    with pytest.raises(ValueError, match="raw text changed"):
        reconcile_crop(root)


# Reject reserved inputs before looking up a crop.
def test_reserved_path_rejected_before_crop_lookup(monkeypatch):
    import course_parser.ocr_recovery as module

    forbidden = (
        Path(module.__file__).resolve().parents[2]
        / "artifacts/validation-transfer-v1/heldout-review"
    )
    monkeypatch.setattr(module, "verify_crop", lambda p: pytest.fail("reserved source opened"))
    with pytest.raises(ValueError, match="reserved cohort"):
        reconcile_crop(forbidden)


# Create an image-only synthetic PDF crop with no native text layer.
def image_only_crop(tmp_path):
    _, _, source = crop(tmp_path)
    scan = tmp_path / "image-only.pdf"
    with fitz.open(source) as original, fitz.open() as pdf:
        image = original[0].get_pixmap(dpi=300)
        page = pdf.new_page(width=200, height=300)
        page.insert_image(page.rect, stream=image.tobytes("png"))
        pdf.save(scan)
    root = tmp_path / "scan-crop"
    render_crop(scan, sha256(scan), 1, Box(x0=10, y0=20, x1=190, y1=130), root, dpi=300)
    return root


# Recover OCR evidence from an image-only PDF while preserving the separate native extraction.
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="optional local OCR executable")
def test_actual_image_only_pdf_recovers_reading_without_replacing_native(tmp_path):
    root = image_only_crop(tmp_path)
    assert route_crop(root)["decision"] == "TARGET_OCR"
    native_sha = sha256(root / "native/extraction.json")
    result = run_tesseract(root, psm=6)
    assert result["status"] == "EXTRACTED"
    assert "Mineralogy" in result["raw_text"]
    recovery = reconcile_crop(root)
    assert recovery["decision"] == "REVIEW_RECOVERED_TEXT"
    assert recovery["primary_text_source"] is None
    assert recovery["course_truth_assigned"] is False
    assert sha256(root / "native/extraction.json") == native_sha


# Avoid starting the optional OCR engine when the native-text checks request no OCR.
def test_pp_initialization_skipped_when_native_gate_requests_no_ocr(tmp_path, monkeypatch):
    from course_parser import crop_ppstructure

    root, _, _ = crop(tmp_path)
    write_json(
        tmp_path / "preparation_manifest.json",
        {
            "crop_paths": [root.name],
            "ocr_eligible_crop_paths": [],
            "file_sha256": artifact_hashes(tmp_path),
        },
    )
    monkeypatch.setattr(
        crop_ppstructure, "_engine", lambda d: pytest.fail("unnecessary model load")
    )
    assert crop_ppstructure.run_crop_ppstructure(tmp_path)["initialization_skipped"] is True


# Check that blank crop cannot become negative course truth.
def test_blank_crop_cannot_become_negative_course_truth(tmp_path):
    source = tmp_path / "blank.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=200, height=300)
        pdf.save(source)
    root = tmp_path / "blank-crop"
    render_crop(source, sha256(source), 1, Box(x0=10, y0=20, x1=190, y1=130), root)
    result = reconcile_crop(root)
    assert result["decision"] == "SOURCE_INPUT_INSUFFICIENT"
    assert result["course_truth_assigned"] is False
    assert result["review_answer"] is None


# Check that reconciliation preserves crop edge uncertainty.
def test_reconciliation_preserves_crop_edge_uncertainty(tmp_path):
    root, _, _ = crop(tmp_path)
    path = root / "native/extraction.json"
    native = read_json(path)
    native["review_flags"] = ["NATIVE_TEXT_INTERSECTS_CROP_EDGE"]
    path.write_text(json.dumps(native))
    other_extraction(root, "ocr", native["raw_text"])
    assert reconcile_crop(root)["decision"] == "REVIEW_CROP_BOUNDS"
