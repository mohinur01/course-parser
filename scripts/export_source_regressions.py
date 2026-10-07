# Save small reproducible regression cases from permitted tuning sources. Preserve the source
# text and geometry instead of using model predictions as expected answers.
"""Freeze source-bound TUNING cases from reviewed PDFs, never model answers."""

from __future__ import annotations

import argparse
import importlib
from dataclasses import asdict
from pathlib import Path

from course_parser.contextual import packet_styles
from course_parser.io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    read_selected_jsonl,
    sha256,
    verify_files,
    write_json,
)
from course_parser.learning import verify_learning_pilot
from course_parser.reference import ReferenceProject

# These are exposed TUNING examples previously inspected against source images.
# Floors record existing diagnostic capability, not production quality targets.
CASES = {
    "v3page_b47d666d22fb2244e175": ("credit_and_metadata", 7),
    "v3page_1fdf722463f408279f9f": ("historical_course_inventory", 10),
    "v3page_e1abace1280edf59e0f0": ("historical_inline_titles", 8),
    "v3page_195d5efc345bd937edfd": ("mixed_requirements_grid_and_prose", 0),
    "v3page_a3360f8d14d197d99b43": ("noncourse_directory_grid", 0),
    "v3page_87a2dd36c5e47a5e4d36": ("distribution_requirements_prose_columns", 0),
}


# Create the traceable outputs described at the top of this script.
def export(reference: ReferenceProject, pilot: Path, evaluation: Path, output: Path):
    verify_learning_pilot(pilot)
    manifest = read_json(evaluation / "evaluation_manifest.json")
    # Reject regression inputs that opened protected evaluation material.
    if manifest["protected_evaluation_opened"] is not False:
        raise ValueError("source regression inputs opened protected evaluation")
    # Stop if the regression source cohort changed.
    if manifest["pilot_manifest_sha256"] != sha256(pilot / "pilot_manifest.json"):
        raise ValueError("regression source cohort changed")
    verify_files(evaluation, manifest["file_sha256"])
    selected = {p["dataset_page_id"]: p for p in read_jsonl(pilot / "selected_pages.jsonl")}
    # Export source regression cases only from permitted tuning examples.
    if any(selected[pid]["learning_partition"] != "TUNING" for pid in CASES):
        raise ValueError("source regressions can contain TUNING cases only")
    truths = read_selected_jsonl(evaluation / "imported_course_truth.jsonl", set(CASES))
    reviews = {
        r["dataset_page_id"]: r
        for r in read_selected_jsonl(evaluation / "imported_page_reviews.jsonl", set(CASES))
    }
    engine = importlib.import_module(
        "catalog_mining.course_extraction_v3.layout_baseline"
    ).PyMuPDFGeometryLayoutEngine()
    output.mkdir(parents=True, exist_ok=False)
    for pid, (kind, floor) in CASES.items():
        item = selected[pid]
        # Stop if the regression page lacks a complete source-reviewed inventory.
        if not reviews[pid]["complete_course_inventory"]:
            raise ValueError("regression page lacks a complete source-reviewed inventory")
        packet = contained_file(pilot, item["packet_path"])
        layouts = tuple(
            engine.analyze_packet(packet, physical_page=p, dataset_page_id=f"{pid}:p{p}")
            for p in item["context_physical_pages"]
        )
        write_json(
            output / f"{kind}.json",
            {
                "schema_version": "source-regression-case-v1",
                "kind": kind,
                "selected": item,
                "minimum_complete_content": floor,
                "source_reference": read_json(packet / "source_reference.json"),
                "source_layouts": [p.model_dump(mode="json") for p in layouts],
                "styles": {
                    nid: asdict(style) for nid, style in packet_styles(packet, layouts).items()
                },
                "page_review": reviews[pid],
                "gold": [g for g in truths if g["dataset_page_id"] == pid],
                "truth_basis": "EXISTING_SOURCE_IMAGE_REVIEW_NOT_PARSER_OUTPUT",
                "independent_gold_certification_claimed": False,
            },
        )
    write_json(
        output / "manifest.json",
        {
            "status": "TUNING_SOURCE_CASES_FROZEN",
            "cases": len(CASES),
            "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
            "evaluation_manifest_sha256": sha256(evaluation / "evaluation_manifest.json"),
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(output),
        },
    )


# Read the command-line options, then save small reproducible regression cases from permitted
# tuning sources.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("reference-project", "pilot", "evaluation", "output"):
        parser.add_argument("--" + arg, required=True, type=Path)
    args = parser.parse_args()
    export(ReferenceProject(args.reference_project), args.pilot, args.evaluation, args.output)


if __name__ == "__main__":
    main()
