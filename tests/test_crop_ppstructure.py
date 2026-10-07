# Tests for the optional crop OCR wrapper and its source-coordinate checks.
"""Independent crop exports cannot silently change coordinate frames or text."""

from test_crop_extractions import crop

from course_parser import crop_ppstructure
from course_parser.crop_extractions import extraction_disagreements
from course_parser.io import artifact_hashes, read_json, write_json


# Build a synthetic crop response with declared image dimensions and recognized word
# locations.
def export(frame, *, unwarp=False):
    polygon = [[20, 30], [200, 30], [200, 60], [20, 60]]
    return {
        "res": {
            "page_index": None,
            "width": frame.pixel_width,
            "height": frame.pixel_height,
            "model_settings": {"use_doc_preprocessor": unwarp, "use_table_recognition": True},
            "doc_preprocessor_res": {
                "model_settings": {
                    "use_doc_orientation_classify": False,
                    "use_doc_unwarping": unwarp,
                }
            },
            "parsing_res_list": [
                {
                    "block_id": 1,
                    "block_label": "text",
                    "block_order": 1,
                    "block_bbox": [20, 30, 200, 60],
                    "block_content": "Mineral0gy",
                }
            ],
            "overall_ocr_res": {
                "rec_texts": ["Mineral0gy"],
                "rec_scores": [0.45],
                "rec_polys": [polygon],
                "dt_polys": [polygon],
                "dt_scores": [0.7],
                "text_rec_score_thresh": 0.0,
            },
        }
    }


# Write the source files and metadata needed by this test.
def prepare(tmp_path):
    root, frame, _ = crop(tmp_path)
    write_json(
        tmp_path / "preparation_manifest.json",
        {
            "crop_paths": ["crop"],
            "file_sha256": artifact_hashes(tmp_path),
        },
    )
    return root, frame


# Check that independent pp crop retains raw and maps back to page.
def test_independent_pp_crop_retains_raw_and_maps_back_to_page(tmp_path, monkeypatch):
    root, frame = prepare(tmp_path)
    monkeypatch.setattr(
        crop_ppstructure,
        "_engine",
        lambda device: ({"paddleocr": "fixture"}, lambda image: export(frame)),
    )
    result = crop_ppstructure.run_crop_ppstructure(tmp_path)
    assert result["statuses"][0]["status"] == "EXTRACTED"
    record = read_json(root / "ppstructure/extraction.json")
    assert record["raw_text"] == "Mineral0gy"
    assert record["lines"][0]["upright_polygon"][0] == [20, 35.5]
    assert (root / "ppstructure/raw.json").is_file()
    assert (root / "ppstructure/parsed_crop_pixels.json").is_file()
    result = extraction_disagreements(root)
    assert result["pairs"][0]["independent_engines"] is True
    assert result["course_truth_assigned"] is False


# Check that unknown unwarping transform retains export as explicit failure.
def test_unknown_unwarping_transform_retains_export_as_explicit_failure(tmp_path, monkeypatch):
    root, frame = prepare(tmp_path)
    monkeypatch.setattr(
        crop_ppstructure,
        "_engine",
        lambda device: ({"paddleocr": "fixture"}, lambda image: export(frame, unwarp=True)),
    )
    result = crop_ppstructure.run_crop_ppstructure(tmp_path)
    assert result["statuses"][0]["status"] == "FAILED"
    assert (root / "ppstructure/raw.json").is_file()
    comparison = extraction_disagreements(root)
    assert comparison["pairs"][0]["status"] == "NOT_COMPARABLE"
    assert any(e["kind"] == "EXTRACTION_FAILED" for e in comparison["review_events"])
