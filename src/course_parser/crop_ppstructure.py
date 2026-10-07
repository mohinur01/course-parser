# Run optional PP-Structure OCR on verified page crops. Save its words, locations and failures
# separately so its output can be compared with the original PDF text.
"""Optional independent crop engine, run in the existing Paddle environment."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from .crop_extractions import verify_crop
from .io import contained_file, read_json, sha256, verify_files, write_json
from .ppstructure_adapter import parse_ppstructure_page

SETTINGS = {
    "lang": "en",
    "engine": "paddle",
    "cpu_threads": 4,
    "enable_mkldnn": False,
    "layout_detection_model_name": "PP-DocLayout_plus-L",
    "text_detection_model_name": "PP-OCRv5_mobile_det",
    "text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
    "use_doc_orientation_classify": False,
    "use_doc_unwarping": False,
    "use_textline_orientation": False,
    "use_region_detection": False,
    "use_table_recognition": False,
    "use_formula_recognition": False,
    "use_chart_recognition": False,
    "use_seal_recognition": False,
    "format_block_content": False,
    "text_rec_score_thresh": 0.0,
}


# Create the optional extraction pipeline with the configured source-preserving settings.
def _engine(device):
    from paddleocr import PPStructureV3

    pipeline = PPStructureV3(device=device, **SETTINGS)

    # Run the configured engine for one verified input and return its recorded output.
    def analyze(image):
        results = list(pipeline.predict(input=str(image)))
        # Expected one PP-Structure result for one crop.
        if len(results) != 1:
            raise ValueError("expected one PP-Structure result for one crop")
        return results[0].json

    return {name: version(name) for name in ("paddleocr", "paddlex", "paddlepaddle")}, analyze


# Decode the crop export and map each retained word and region into the original PDF
# coordinate frame.
def decode_crop_export(exported, frame, page_key, engine_version):
    parsed = parse_ppstructure_page(
        exported,
        page_key=page_key,
        image_width=frame.pixel_width,
        image_height=frame.pixel_height,
        width=frame.pixel_width,
        height=frame.pixel_height,
        engine_version=engine_version,
    )
    # Require the optional tool's separate OCR output rather than treating layout text as an
    # OCR reading.
    if parsed.ocr is None:
        raise ValueError("PP-Structure returned no independent OCR representation")
    lines = []
    for line in parsed.ocr.lines:
        polygon = [list(point) for point in line.polygon]
        lines.append(
            {
                "line_id": line.line_id,
                "text": line.text,
                "recognition_score": line.recognition_score,
                "crop_polygon": polygon,
                "upright_polygon": frame.map_polygon(polygon),
                "pdf_user_space_polygon": frame.map_polygon(polygon, pdf_user_space=True),
            }
        )
    return parsed, lines


# Run optional PP-Structure OCR for the verified crops and preserve each result or failure.
def run_crop_ppstructure(run: Path, *, limit: int | None = None, device="cpu"):
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(run)
    # Stop if the review run is sealed; prepare a fresh run to add extractions.
    if (run / "review_manifest.json").exists():
        raise ValueError("review run is sealed; prepare a fresh run to add extractions")
    preparation = read_json(run / "preparation_manifest.json")
    verify_files(run, preparation["file_sha256"])
    # Crop limit must be positive.
    if limit is not None and limit < 1:
        raise ValueError("crop limit must be positive")
    # New source-gated preparations explicitly identify eligible crops. Legacy bounded OCR
    # experiments retain their recorded explicit selection.
    crops = preparation.get("ocr_eligible_crop_paths", preparation["crop_paths"])[:limit]
    # Reject a path or coordinate that falls outside its allowed source boundary.
    if any(c not in preparation["crop_paths"] for c in crops):
        raise ValueError("OCR eligibility escapes the prepared source crops")
    if not crops:
        return {"crops_attempted": 0, "statuses": [], "initialization_skipped": True}
    packages, analyze, initialization_error = {}, None, None
    try:
        packages, analyze = _engine(device)
    except Exception as exc:
        initialization_error = f"{type(exc).__name__}: {exc}"
    statuses = []
    for relative in crops:
        root = contained_file(run, relative)
        frame = verify_crop(root)
        directory = root / "ppstructure"
        directory.mkdir(exist_ok=False)
        raw, lines, flags, error = None, [], [], initialization_error
        try:
            if analyze is None:
                raise RuntimeError(initialization_error)
            exported = analyze(root / "crop.png")
            write_json(directory / "raw.json", exported)
            # Parse in crop pixels, then apply the recorded crop transform.
            # Existing adapter rejects unknown orientation/unwarping frames.
            parsed, lines = decode_crop_export(exported, frame, root.name, packages["paddleocr"])
            write_json(directory / "parsed_crop_pixels.json", parsed.model_dump(mode="json"))
            raw = parsed.ocr.raw_text
            flags = list(parsed.ocr.review_flags)
            (directory / "raw.txt").write_text(raw, encoding="utf-8")
            status = "EXTRACTED"
        except Exception as exc:
            status, error = "FAILED", f"{type(exc).__name__}: {exc}"
        verify_crop(root)
        record = {
            "engine": "PP_STRUCTURE_V3",
            "engine_version": packages.get("paddleocr"),
            "packages": packages,
            "settings": {**SETTINGS, "device": device},
            "status": status,
            "input_crop_sha256": frame.crop_image_sha256,
            "transform_sha256": sha256(root / "transform.json"),
            "raw_text": raw,
            "raw_text_sha256": sha256(directory / "raw.txt")
            if (directory / "raw.txt").exists()
            else None,
            "raw_export_sha256": sha256(directory / "raw.json")
            if (directory / "raw.json").exists()
            else None,
            "lines": lines,
            "review_flags": flags,
            "error": error,
            "authoritative_text_replaced": False,
        }
        write_json(directory / "extraction.json", record)
        statuses.append({"crop_path": relative, "status": status, "error": error})
    return {"crops_attempted": len(statuses), "statuses": statuses}
