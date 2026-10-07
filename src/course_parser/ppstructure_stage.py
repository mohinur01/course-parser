# Import optional PP-Structure results for the verified source pages. Check file identities
# and coordinate systems, then retain layout and OCR suggestions as separate evidence.
"""Bind optional PP-Structure exports to verified rendered page inputs."""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import fitz

from .bridge import READY
from .io import contained_file, read_json, sha256
from .layout_models import PagePredictions, TextCell
from .ppstructure_adapter import compare_layouts, parse_ppstructure_page


# Read the export index that identifies the PP-Structure result saved for each page.
def load_ppstructure_manifest(path: Path | None, pages: list[dict], evidence: Path):
    if path is None:
        return {}, {}
    value = read_json(path)
    # Reject unsupported PP-Structure manifest or unspecified image frame.
    if (
        value.get("schema_version") != "course-parser-ppstructure-input-v1"
        or value.get("coordinate_frame") != "VERIFIED_UPRIGHT_IMAGE"
        or value.get("input_kind") != "RENDERED_PAGE_IMAGE"
    ):
        raise ValueError("unsupported PP-Structure manifest or unspecified image frame")
    inventory = {page["page_key"]: page for page in pages}
    imports, inputs = {}, {str(path.resolve()): sha256(path)}
    for entry in value["pages"]:
        key = entry["page_key"]
        page = inventory.get(key)
        # Stop if the PP-Structure import does not bind to one verified evidence image.
        if (
            key in imports
            or page is None
            or page["evidence_status"] != READY
            or entry["source_pdf_checksum"] != page["source_pdf_checksum"]
            or entry["image_sha256"] != page["image_sha256"]
            or not isinstance(entry["engine_version"], str)
            or not entry["engine_version"]
        ):
            raise ValueError("PP-Structure import does not bind to one verified evidence image")
        if entry.get("status") == "FAILED":
            # Stop if the PP-Structure producer failure has no error reason.
            if not isinstance(entry.get("error"), str) or not entry["error"]:
                raise ValueError("PP-Structure producer failure has no error reason")
            imports[key] = entry
            continue
        # Stop if the PP-Structure export status or image dimensions are invalid.
        if (
            entry.get("status", "EXPORTED") != "EXPORTED"
            or type(entry["image_width"]) is not int
            or type(entry["image_height"]) is not int
        ):
            raise ValueError("PP-Structure export status or image dimensions are invalid")
        image = fitz.Pixmap(str(contained_file(evidence, page["image_relative_path"])))
        # The export's page dimensions must match the verified source image.
        if (entry["image_width"], entry["image_height"]) != (image.width, image.height):
            raise ValueError("PP-Structure declared dimensions differ from the verified image")
        raw_path = contained_file(path.parent, entry["result_json_path"])
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(raw_path) != entry["result_json_sha256"]:
            raise ValueError("PP-Structure JSON checksum changed")
        imports[key] = {**entry, "raw_path": raw_path}
        inputs[str(raw_path)] = entry["result_json_sha256"]
    return imports, inputs


# Bind an external layout export to the approved page and retain its OCR and layout proposals
# as separate evidence.
def import_ppstructure(record, entry, native, output):
    binding = {key: value for key, value in entry.items() if key != "raw_path"}
    record["ppstructure_binding"] = binding
    # Keep an optional-engine failure explicit rather than trying to import it as a successful
    # export.
    if entry.get("status") == "FAILED":
        raise ValueError(f"PP-Structure producer failed: {entry['error']}")
    destination = output / "ppstructure_exports" / (entry["result_json_sha256"] + ".json")
    destination.parent.mkdir(exist_ok=True)
    if not destination.exists():
        shutil.copyfile(entry["raw_path"], destination)
    # Stop if the PP-Structure JSON changed while being archived.
    if sha256(destination) != entry["result_json_sha256"]:
        raise ValueError("PP-Structure JSON changed while being archived")
    record["ppstructure_export_relative_path"] = str(destination.relative_to(output))
    proposal = parse_ppstructure_page(
        read_json(destination),
        page_key=record["page_key"],
        image_width=entry["image_width"],
        image_height=entry["image_height"],
        width=native.width,
        height=native.height,
        cells=native.cells,
        engine_version=entry["engine_version"],
    )
    record["ppstructure"] = proposal.model_dump(mode="json")
    record["ppstructure_status"] = "IMPORTED"
    primary = (
        PagePredictions.model_validate(record["predictions"]) if record["predictions"] else None
    )
    record["layout_comparison"] = compare_layouts(primary, proposal.layout)
    flags = proposal.ocr.review_flags if proposal.ocr else ("PP_STRUCTURE_OCR_UNAVAILABLE",)
    record["ppstructure_routing"] = "OCR_REVIEW_REQUIRED" if flags else "LAYOUT_REVIEW_REQUIRED"


# Check that the imported PP-Structure result belongs to the expected page and image.
def verify_ppstructure_page(output, record):
    status = record.get("ppstructure_status", "NOT_REQUESTED")
    # Reject unknown PP-Structure page status.
    if status not in {"NOT_REQUESTED", "IMPORTED", "INPUT_INCOMPLETE"}:
        raise ValueError("unknown PP-Structure page status")
    if status == "NOT_REQUESTED":
        # Stop if the unrequested PP-Structure evidence is present.
        if record.get("ppstructure") is not None or record.get("ppstructure_binding") is not None:
            raise ValueError("unrequested PP-Structure evidence is present")
        return
    entry = record.get("ppstructure_binding")
    if entry is None:
        # Stop if the PP-Structure input status has no binding or failure reason.
        if status != "INPUT_INCOMPLETE" or not record["errors"]:
            raise ValueError("PP-Structure input status has no binding or failure reason")
        return
    # Stop if the PP-Structure source/image binding differs.
    if (
        entry["page_key"] != record["page_key"]
        or entry["source_pdf_checksum"] != record["source_pdf_checksum"]
        or entry["image_sha256"] != record["image_sha256"]
    ):
        raise ValueError("PP-Structure source/image binding differs")
    if entry.get("status") == "FAILED":
        # Retain the optional engine's failure in the imported result.
        if (
            status != "INPUT_INCOMPLETE"
            or record.get("ppstructure") is not None
            or not entry.get("error")
            or not record["errors"]
            or record["ppstructure_routing"] != "MODEL_REPAIR_REQUIRED"
        ):
            raise ValueError("PP-Structure producer failure was not preserved")
        return
    image = fitz.Pixmap(str(contained_file(output, record["image_relative_path"])))
    # The imported result and source image must describe the same page size.
    if (image.width, image.height) != (entry["image_width"], entry["image_height"]):
        raise ValueError("PP-Structure image dimensions differ")
    archived = contained_file(output, record["ppstructure_export_relative_path"])
    # Reject this input if its file fingerprint no longer matches the recorded version.
    if sha256(archived) != entry["result_json_sha256"]:
        raise ValueError("PP-Structure archived JSON checksum differs")
    try:
        expected = parse_ppstructure_page(
            read_json(archived),
            page_key=record["page_key"],
            image_width=entry["image_width"],
            image_height=entry["image_height"],
            width=record["width"],
            height=record["height"],
            cells=tuple(TextCell.model_validate(c) for c in record["native_cells"]),
            engine_version=entry["engine_version"],
        )
    except (ValueError, KeyError, TypeError) as exc:
        # Keep a malformed export recorded as a parse failure.
        if (
            status != "INPUT_INCOMPLETE"
            or record.get("ppstructure") is not None
            or not record["errors"]
        ):
            raise ValueError("PP-Structure parse failure was not preserved") from exc
        return
    # Stop if the PP-Structure evidence differs from its archived model export.
    if status != "IMPORTED" or record["ppstructure"] != expected.model_dump(mode="json"):
        raise ValueError("PP-Structure evidence differs from its archived model export")
    primary = (
        PagePredictions.model_validate(record["predictions"]) if record["predictions"] else None
    )
    # Stop if the layout comparison differs from the two recorded engine proposals.
    if record["layout_comparison"] != compare_layouts(primary, expected.layout):
        raise ValueError("layout comparison differs from the two recorded engine proposals")
    flags = expected.ocr.review_flags if expected.ocr else ("PP_STRUCTURE_OCR_UNAVAILABLE",)
    routing = "OCR_REVIEW_REQUIRED" if flags else "LAYOUT_REVIEW_REQUIRED"
    # Stop if the PP-Structure routing differs from its OCR evidence.
    if record["ppstructure_routing"] != routing:
        raise ValueError("PP-Structure routing differs from its OCR evidence")


# Count the retained layout, OCR, warning and routing records in the engine result.
def ppstructure_counts(records):
    imported = [r["ppstructure"] for r in records if r.get("ppstructure") is not None]
    return {
        "ppstructure_pages_imported": len(imported),
        "ppstructure_page_status_counts": dict(
            Counter(r.get("ppstructure_status", "NOT_REQUESTED") for r in records)
        ),
        "ppstructure_ocr_lines": sum(len(p["ocr"]["lines"]) for p in imported if p["ocr"]),
        "ppstructure_ocr_detections": sum(
            len(p["ocr"]["detections"]) for p in imported if p["ocr"]
        ),
        "ppstructure_ocr_review_flags": sum(
            len(p["ocr"]["review_flags"]) for p in imported if p["ocr"]
        ),
        "ppstructure_specialized_regions": sum(len(p["specialized_routes"]) for p in imported),
        "engine_comparison_status_counts": dict(
            Counter(
                r["layout_comparison"]["status"]
                for r in records
                if r.get("layout_comparison") is not None
            )
        ),
    }
