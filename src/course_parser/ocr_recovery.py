# Decide when a small part of a page needs OCR because its original text is missing or
# damaged. Compare separate readings for review without automatically replacing native PDF
# text.
"""Source-bound OCR routing and non-destructive reconciliation of crop evidence.

Quality signals request evidence recovery, not replacement text. Two segmentation
modes of Tesseract are one engine family. Neither agreement nor recognition scores
authorize a correction, course owner, new keyword hit, or training label.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from pathlib import Path

from .crop_extractions import extraction_disagreements, verify_crop
from .io import read_json, sha256

VERSION = "source-targeted-ocr-recovery-v1"


# Describe whether native crop text is readable, missing, damaged or spatially uncertain.
def native_quality(record):
    text = record.get("raw_text")
    damaged = [
        {"offset": i, "character": c, "unicode": f"U+{ord(c):04X}"}
        for i, c in enumerate(text or "")
        if c == "\ufffd" or (unicodedata.category(c) in {"Cc", "Co"} and c not in "\t\n\r")
    ]
    return {
        "status": record["status"],
        "native_text_present": bool(text and text.strip()),
        "damaged_characters": damaged,
        "crop_edge_intersection": "NATIVE_TEXT_INTERSECTS_CROP_EDGE"
        in record.get("review_flags", []),
        "quality_is_truth": False,
    }


# Use local character/geometry evidence; a missing course alone is not OCR need.
def route_native_record(record, *, layout=None, omission_kind=None):
    """Use local character/geometry evidence; a missing course alone is not OCR need."""
    quality = native_quality(record)
    reasons, crossed = [], []
    if record["status"] != "EXTRACTED":
        reasons.append("NATIVE_EXTRACTION_FAILED")
    elif not quality["native_text_present"]:
        reasons.append("NATIVE_TEXT_ABSENT_IMAGE_PROBE_REQUIRED")
    if quality["damaged_characters"]:
        reasons.append("NATIVE_DAMAGED_CHARACTERS")
    table = False
    if layout:
        table = any(r["role"] == "TABLE_CELL" for r in layout["regions"])
        regions = [r for r in layout["regions"] if r.get("column_id") is not None]
        for line in record.get("lines", []):
            columns = set()
            for cell in line["source_cells"]:
                b = cell["box"]
                x, y = (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2
                owners = {
                    r["column_id"]
                    for r in regions
                    if r["geometry"]["x0"] <= x <= r["geometry"]["x1"]
                    and r["geometry"]["y0"] <= y <= r["geometry"]["y1"]
                }
                if len(owners) == 1:
                    columns.update(owners)
            if len(columns) > 1:
                crossed.append({"line_id": line["line_id"], "columns": sorted(columns)})
        if crossed and not table:
            reasons.append("NATIVE_LINE_CROSSES_INFERRED_PROSE_COLUMNS")
    # Clipped context can manufacture disagreement. Resolve crop coverage first.
    if quality["crop_edge_intersection"]:
        decision = "REVIEW_CROP_BOUNDS"
    elif crossed and table:
        decision = "REVIEW_TABLE_STRUCTURE"
    elif crossed:
        decision = "REVIEW_COLUMN_CROP"
    elif reasons:
        decision = "TARGET_OCR"
    elif omission_kind:
        decision = "REVIEW_STRUCTURE_ONLY"
    else:
        decision = "KEEP_NATIVE"
    return {
        "version": VERSION,
        "decision": decision,
        "ocr_eligible": decision == "TARGET_OCR",
        "reasons": reasons,
        "quality": quality,
        "cross_column_lines": crossed,
        "table_context": table,
        "omission_kind": omission_kind,
        "automatic_text_replacement": False,
        "note": "Native availability is not proof of correct transcription or ownership. "
        "Empty native crops may be blank; OCR is a bounded evidence probe, not a positive label.",
    }


# Reject reserved or unbound inputs before recovering or comparing crop evidence.
def _guard(*paths):
    # Runtime import avoids a cycle with the omission producer using this stage.
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(*paths)


# Choose whether a crop needs no OCR, a bounded OCR probe or source/structure review first.
def route_crop(root: Path, *, layout=None, omission_kind=None):
    _guard(root)
    frame = verify_crop(root)
    extraction_disagreements(root)  # Revalidate raw slices, hashes and every available output.
    # OCR routing must use the same source page and coordinate system as the native text.
    if layout and (
        layout["source_pdf_checksum"] != frame.source_pdf_checksum
        or layout["physical_page"] != frame.physical_page
        or abs(layout["page_width"] - frame.page_width) > 1e-5
        or abs(layout["page_height"] - frame.page_height) > 1e-5
    ):
        raise ValueError("OCR routing layout identifies a different source or coordinate frame")
    native = read_json(root / "native/extraction.json")
    # OCR routing requires a native PDF extraction.
    if native["engine"] != "PYMUPDF_NATIVE":
        raise ValueError("OCR routing requires a native PDF extraction")
    return {
        **route_native_record(native, layout=layout, omission_kind=omission_kind),
        "source_pdf_checksum": frame.source_pdf_checksum,
        "physical_page": frame.physical_page,
        "crop_image_sha256": frame.crop_image_sha256,
        "transform_sha256": sha256(root / "transform.json"),
        "native_extraction_sha256": sha256(root / "native/extraction.json"),
    }


# Retain competing readings and suggest review; never assemble consensus text.
def reconcile_crop(root: Path, *, layout=None, omission_kind=None):
    """Retain competing readings and suggest review; never assemble consensus text."""
    route = route_crop(root, layout=layout, omission_kind=omission_kind)
    comparisons = extraction_disagreements(root)
    records = {p.parent.name: read_json(p) for p in sorted(root.glob("*/extraction.json"))}
    readings = defaultdict(list)
    for name, record in records.items():
        if record["status"] == "EXTRACTED" and (record.get("raw_text") or "").strip():
            # Whitespace equivalence is diagnostic; every raw reading is retained.
            readings[" ".join(record["raw_text"].split())].append(name)
    alternatives = []
    native_text = " ".join((records["native"].get("raw_text") or "").split())
    for text, names in readings.items():
        if text == native_text:
            continue
        # Different settings of the same OCR tool do not count as independent engines.
        families = sorted({records[name]["engine"] for name in names})
        alternatives.append(
            {
                "comparison_text": text,
                "raw_readings": [
                    {"extraction_id": n, "raw_text": records[n]["raw_text"]} for n in names
                ],
                "supporting_engine_families": families,
                "independent_engine_count": len(families),
                "source_review_required": True,
            }
        )
    if route["decision"] in {"REVIEW_CROP_BOUNDS", "REVIEW_TABLE_STRUCTURE", "REVIEW_COLUMN_CROP"}:
        decision = route["decision"]
    elif not native_text:
        decision = "REVIEW_RECOVERED_TEXT" if alternatives else "SOURCE_INPUT_INSUFFICIENT"
    elif any(a["independent_engine_count"] >= 2 for a in alternatives):
        decision = "REVIEW_CORROBORATED_ALTERNATIVE"
    elif route["ocr_eligible"]:
        decision = "REVIEW_NATIVE_QUALITY"
    elif alternatives:
        decision = "KEEP_NATIVE_WITH_OCR_DISAGREEMENT"
    else:
        decision = "KEEP_NATIVE"
    if len(records) == 1 and not route["ocr_eligible"]:
        # Intentional omission of redundant OCR is not a failed/missing engine.
        comparisons["review_events"] = [
            e for e in comparisons["review_events"] if e["kind"] != "SECOND_EXTRACTION_MISSING"
        ]
        comparisons["review_status"] = (
            "PENDING_SOURCE_REVIEW"
            if comparisons["review_events"]
            else "NO_EXTRACTION_DISAGREEMENT"
        )
    return {
        "version": VERSION,
        "decision": decision,
        "route": route,
        "comparisons": comparisons,
        "alternative_readings": alternatives,
        "extraction_sha256": {name: sha256(root / name / "extraction.json") for name in records},
        "primary_text_source": "native" if native_text else None,
        "ocr_missing_by_design": len(records) == 1 and not route["ocr_eligible"],
        "failed_extraction_ids": [n for n, r in records.items() if r["status"] != "EXTRACTED"],
        "review_status": "PENDING_SOURCE_REVIEW",
        "review_answer": None,
        "consensus_text_created": False,
        "automatic_text_replacement": False,
        "course_truth_assigned": False,
        "keyword_hit_dispositions_changed": False,
    }
