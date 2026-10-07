# Compare cached external reading-order suggestions on the permitted original sample. Record
# disagreements as review evidence without changing the native ordering path.
"""Optional cached-engine evidence over the fixed, permitted original pilot.

No engine installation, inference, answer import, parser fallback or arbitrary
input-path discovery occurs. The two exports already cover 60 reviewed pages.
"""

from __future__ import annotations

import importlib
from collections import Counter
from pathlib import Path

from course_parser.engine_order import external_order
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
from course_parser.order_audit import compare_order_relations

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE = WORKSPACE / "artifacts/continuation-phase8-v1/candidate-v1/original-evaluation"
PILOT = WORKSPACE / "artifacts/source-learning-v4/pilot"
EXPORTS = {
    "docling": WORKSPACE / "artifacts/ownership-order-v2/docling-exports-v2",
    "ppstructure": WORKSPACE / "artifacts/ownership-order-v2/pp-default-native-order-exports",
}


# Compare optional ordering tools against the native text on the fixed permitted sample.
def audit_exposed_order(predictions, output):
    enforce_policy(BASELINE, output)
    # This is a new prediction run over the same approved existing pilot.
    enforce_policy(BASELINE, predictions)
    approved = read_json(BASELINE / "evaluation_manifest.json")
    manifest = read_json(predictions / "prediction_manifest.json")
    # Order audit requires source-only native predictions on the approved original pilot.
    if (
        manifest["pilot_manifest_sha256"] != approved["pilot_manifest_sha256"]
        or (manifest["pilot_manifest_sha256"] != sha256(PILOT / "pilot_manifest.json"))
        or manifest["ordering_engine"] != "source_rows"
        or manifest["source_truth_read"] is not False
    ):
        raise ValueError(
            "order audit requires source-only native predictions on the approved original pilot"
        )
    verify_files(predictions, manifest["file_sha256"])
    selected = {r["dataset_page_id"]: r for r in read_jsonl(PILOT / "selected_pages.jsonl")}
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    exports = {name: read_json(path / "manifest.json") for name, path in EXPORTS.items()}
    for name, value in exports.items():
        # Stop if the cached exports do not identify the same source-only pilot.
        if (
            value.get("source_truth_read") is not False
            or value.get("engine") != name
            or value.get("pilot_manifest_sha256") != manifest["pilot_manifest_sha256"]
        ):
            raise ValueError("cached exports do not identify the same source-only pilot")
    output.mkdir(parents=True, exist_ok=False)
    reports, opinions, unavailable, tasks = [], [], [], []
    for page in read_jsonl(predictions / "page_proposals.jsonl"):
        item = selected[page["dataset_page_id"]]
        packet = contained_file(PILOT, item["packet_path"])
        for layout in page.get("layouts", []):
            native = module.PageLayoutProposal.model_validate(layout)
            key = f"{item['dataset_page_id']}:p{native.physical_page}"
            for name, export_root in EXPORTS.items():
                if key not in exports[name]["pages"]:
                    unavailable.append(
                        {
                            "dataset_page_id": item["dataset_page_id"],
                            "physical_page": native.physical_page,
                            "engine": name,
                            "reason": "NO_CACHED_EXPORT_NOT_A_SOURCE_NEGATIVE",
                        }
                    )
                    continue
                alternative = external_order(native, item, packet, export_root, name, module)
                report = compare_order_relations(native, alternative)
                report.update(
                    requested_engine=name,
                    window_id=item["dataset_page_id"],
                    native_prediction_manifest_sha256=sha256(
                        predictions / "prediction_manifest.json"
                    ),
                    export_manifest_sha256=sha256(export_root / "manifest.json"),
                )
                reports.append(report)
                # Preserve the complete alternate graph separately; native graph is retained
                # in the sealed prediction bundle, never overwritten.
                opinions.append(
                    {
                        "window_id": item["dataset_page_id"],
                        "engine": name,
                        "native_layout_reference": str(predictions / "page_proposals.jsonl"),
                        "alternative_layout": alternative.model_dump(mode="json"),
                    }
                )
                if report["review_flags"]:
                    stable = "|".join(
                        [
                            item["source_pdf_checksum"],
                            str(native.physical_page),
                            name,
                            report["source_cells_sha256"],
                            report["native_order_sha256"],
                            report["alternative_order_sha256"],
                        ]
                    )
                    import hashlib

                    task_id = "order-" + hashlib.sha256(stable.encode()).hexdigest()[:24]
                    tasks.append(
                        {
                            "task_id": task_id,
                            "window_id": item["dataset_page_id"],
                            "source_pdf_checksum": item["source_pdf_checksum"],
                            "physical_page": native.physical_page,
                            "engine": name,
                            "review_flags": report["review_flags"],
                            "review_status": "PENDING_SOURCE_REVIEW",
                            "review_answer": None,
                            "course_truth_created": False,
                            "report_index": len(reports) - 1,
                            "counts": report["counts"],
                        }
                    )
    write_jsonl(output / "order_diagnostics.jsonl", reports)
    write_jsonl(output / "order_opinions.jsonl", opinions)
    # Identical repeated page/graph observations can share one review question.
    unique = {}
    for task in tasks:
        if task["task_id"] not in unique:
            unique[task["task_id"]] = {**task, "occurrences": []}
        unique[task["task_id"]]["occurrences"].append(
            {"window_id": task["window_id"], "report_index": task["report_index"]}
        )
    write_jsonl(output / "review_tasks.jsonl", list(unique.values()))
    write_jsonl(output / "unavailable_exports.jsonl", unavailable)
    summary = {
        "status": "OPTIONAL_SECOND_OPINIONS_REVIEW_ONLY",
        "source_truth_read": False,
        "reserved_cohort_access": "SEALED",
        "native_order_replaced": False,
        "course_truth_created": False,
        "text_replaced": False,
        "automatic_engine_selection": False,
        "native_prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "export_manifest_sha256": {
            name: sha256(p / "manifest.json") for name, p in EXPORTS.items()
        },
        "reports": len(reports),
        "unique_review_tasks": len(unique),
        "unavailable_exports": len(unavailable),
        "engines": {
            name: {
                "reports": sum(r["requested_engine"] == name for r in reports),
                "decisions": dict(
                    Counter(r["decision"] for r in reports if r["requested_engine"] == name)
                ),
                "flags": dict(
                    Counter(
                        flag
                        for r in reports
                        if r["requested_engine"] == name
                        for flag in r["review_flags"]
                    )
                ),
                "relation_counts": dict(
                    Counter(
                        {
                            k: sum(r["counts"][k] for r in reports if r["requested_engine"] == name)
                            for k in reports[0]["counts"]
                        }
                    )
                )
                if reports
                else {},
            }
            for name in EXPORTS
        },
        "files_sha256": artifact_hashes(output),
    }
    write_json(output / "audit_manifest.json", summary)
    return summary
