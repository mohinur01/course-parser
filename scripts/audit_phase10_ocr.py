# Compare the saved OCR readings for the fixed tuning crops. Keep disagreements and failures
# visible without importing review answers or replacing source text.
"""Reconcile the fixed exposed TUNING crop experiment without importing answers."""

from collections import Counter
from pathlib import Path

from course_parser.field_scoring import enforce_policy
from course_parser.io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from course_parser.ocr_recovery import reconcile_crop

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE = WORKSPACE / "artifacts/reading-order-phase9-v1/candidate-v2/original-evaluation"
CACHE = WORKSPACE / "artifacts/omission-extractions-v1/release-v4"
PILOT = WORKSPACE / "artifacts/source-learning-v4/pilot"


# Compare the saved OCR readings and coordinate mappings for the fixed tuning crops.
def audit_cached_crops(predictions, output):
    enforce_policy(BASELINE, output)
    enforce_policy(BASELINE, predictions)
    pm = read_json(predictions / "prediction_manifest.json")
    prep = read_json(CACHE / "preparation_manifest.json")
    review = read_json(CACHE / "review_manifest.json")
    # Stop if the crop cache does not identify the permitted source-only TUNING pilot.
    if (
        pm["pilot_manifest_sha256"] != sha256(PILOT / "pilot_manifest.json")
        or prep["pilot_manifest_sha256"] != pm["pilot_manifest_sha256"]
        or prep["source_truth_read"] is not False
        or prep["crop_partition"] != "TUNING"
        or review["source_truth_read"] is not False
        or review["preparation_manifest_sha256"] != sha256(CACHE / "preparation_manifest.json")
    ):
        raise ValueError("crop cache does not identify the permitted source-only TUNING pilot")
    verify_files(CACHE, review["file_sha256"])
    verify_files(predictions, pm["file_sha256"])
    selected = {r["dataset_page_id"]: r for r in read_jsonl(PILOT / "selected_pages.jsonl")}
    flags = {r["flag_id"]: r for r in read_jsonl(CACHE / "omission_flags.jsonl")}
    proposals = {r["dataset_page_id"]: r for r in read_jsonl(predictions / "page_proposals.jsonl")}
    output.mkdir(parents=True, exist_ok=False)
    reconciliations, retained = [], []
    for relative in prep["crop_paths"]:
        root = contained_file(CACHE, relative)
        flag = flags[root.name]
        window = flag["dataset_page_id"]
        # Reject a path or coordinate that falls outside its allowed source boundary.
        if flag["learning_partition"] != "TUNING" or (
            flag["source_pdf_checksum"] != selected[window]["source_pdf_checksum"]
        ):
            raise ValueError("cached crop escaped permitted source identity or partition")
        layout = next(
            entry
            for entry in proposals[window]["layouts"]
            if entry["physical_page"] == flag["physical_page"]
        )
        result = reconcile_crop(root, layout=layout, omission_kind=flag["kind"])
        reconciliations.append(
            {
                "flag_id": flag["flag_id"],
                "window_id": window,
                "crop_path": str(root),
                "learning_partition": "TUNING",
                **result,
            }
        )
        retained.extend(
            {"flag_id": flag["flag_id"], "path": str(p), "sha256": sha256(p)}
            for p in root.rglob("*")
            if p.is_file()
        )
    write_jsonl(output / "reconciliations.jsonl", reconciliations)
    write_jsonl(output / "preserved_artifacts.jsonl", retained)
    manifest = {
        "source_truth_read": False,
        "reserved_cohort_access": "SEALED",
        "crop_count": len(reconciliations),
        "routing_counts": dict(Counter(r["route"]["decision"] for r in reconciliations)),
        "reconciliation_counts": dict(Counter(r["decision"] for r in reconciliations)),
        "native_extractions": len(reconciliations),
        "retained_extraction_records": sum(len(r["extraction_sha256"]) for r in reconciliations),
        "hypothetical_tesseract_calls_if_every_crop_were_ocr": 2 * len(reconciliations),
        "source_gate_tesseract_calls_on_same_crops": 2
        * sum(r["route"]["ocr_eligible"] for r in reconciliations),
        "new_ocr_inference_performed_on_cached_crops": False,
        "automatic_text_replacement": False,
        "training_labels_created": 0,
        "comparison_note": "Retrospective source-only routing audit; not an accuracy measurement.",
        "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "cache_preparation_manifest_sha256": sha256(CACHE / "preparation_manifest.json"),
        "cache_review_manifest_sha256": sha256(CACHE / "review_manifest.json"),
        "files_sha256": artifact_hashes(output),
    }
    write_json(output / "audit_manifest.json", manifest)
    return manifest
