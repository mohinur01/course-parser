# Choose additional pages for review using information available before annotation. Keep
# random selections and targeted selections separate so their results can be interpreted
# honestly.
"""Source-only review strata; independent random pages are never reranked away."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    sha256,
    write_json,
    write_jsonl,
)
from .learning import balanced_sample
from .omission_review import _verified_predictions
from .omission_signals import audit_page


# Keep random, active TUNING and sealed VALIDATION lanes separate.
def build_expanded_review(pilot: Path, predictions: Path, output: Path) -> dict:
    """Keep random, active TUNING and sealed VALIDATION lanes separate."""
    _verified_predictions(pilot, predictions)
    selected = {p["dataset_page_id"]: p for p in read_jsonl(pilot / "selected_pages.jsonl")}
    rows, flags = [], []
    for proposal in read_jsonl(predictions / "page_proposals.jsonl"):
        item = selected[proposal["dataset_page_id"]]
        packet = contained_file(pilot, item["packet_path"])
        source = read_json(packet / "source_reference.json")
        page = item["central_physical_page"]
        candidates = [c for c in proposal.get("candidates", []) if c["physical_page"] == page]
        signals = set()
        if any(str(c["identifier_kind"]).startswith("HISTORICAL") for c in candidates):
            signals.add("HISTORICAL_IDENTIFIERS")
        if item["original_hit_count"] and not candidates:
            signals.add("KEYWORD_WITHOUT_HEADER_PROPOSAL")
        layouts = [entry for entry in proposal.get("layouts", []) if entry["physical_page"] == page]
        if layouts:
            layout = layouts[0]
            if any(r["role"] == "TABLE_CELL" for r in layout["regions"]):
                signals.add("GRID_PURPOSE_AND_ROW_OWNERSHIP")
            if any(
                x["physical_page"] == page and x["reading_order_changed"]
                for x in proposal.get("layout_interpretations", [])
            ):
                signals.add("COMPETING_LAYOUT_INTERPRETATION")
            if layout["status"] != "COMPLETE":
                signals.add("UNRESOLVED_ORDER")
            # Native omission signals use no answers and need no proposed header.
            if item["learning_partition"] == "TUNING":
                asset = next(a for a in source["page_artifacts"] if a["physical_page"] == page)
                binding = {
                    k: item[k]
                    for k in (
                        "dataset_page_id",
                        "source_document_id",
                        "source_pdf_checksum",
                        "learning_group",
                        "learning_partition",
                    )
                }
                page_flags = audit_page(
                    read_json(contained_file(packet, Path(asset["embedded_geometry_path"]).name)),
                    layout,
                    proposal,
                    binding,
                )
                flags.extend(page_flags)
                signals.update(s for f in page_flags for s in f["signals"])
        if proposal["status"] != "PROPOSED":
            signals.add("PARSER_FAILURE")
        if any(s["status"] != "UNIQUE" for s in proposal.get("field_sequence_proposals", [])):
            signals.add("FIELD_TRANSITION_REVIEW")
        lane = (
            "RANDOM"
            if item["pilot_role"] == "RANDOM"
            else "VALIDATION_SEALED_REVIEW"
            if item["learning_partition"] == "VALIDATION"
            else "ACTIVE_TUNING"
        )
        rows.append(
            {
                "dataset_page_id": item["dataset_page_id"],
                "learning_group": item["learning_group"],
                "learning_partition": item["learning_partition"],
                "lane": lane,
                "original_selection_role": item["selection_role"],
                "cohort": item["cohort"],
                "source_document_id": item["source_document_id"],
                "source_pdf_checksum": item["source_pdf_checksum"],
                "physical_page": page,
                "original_page_task_id": item["original_page_task_id"],
                "original_hit_count": item["original_hit_count"],
                "signals": sorted(signals),
                "source_packet": str(packet),
                "reviewer_id": None,
                "source_review_decision": None,
                "may_train": item["learning_partition"] == "TUNING",
                "is_accuracy_sample": False,
            }
        )
    ordered = []
    for lane in ("RANDOM", "ACTIVE_TUNING", "VALIDATION_SEALED_REVIEW"):
        subset = [r for r in rows if r["lane"] == lane]
        # Same source-only seed irrespective of model score or learned error.
        ordered.extend(
            {**r, "lane_rank": rank}
            for rank, r in enumerate(
                balanced_sample(subset, len(subset), "expanded-source-review-v1:" + lane), 1
            )
        )
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "review_queue.jsonl", ordered)
    write_jsonl(output / "native_omission_flags.jsonl", flags)
    manifest = {
        "status": "SOURCE_REVIEW_QUEUE_READY",
        "page_windows": len(ordered),
        "lane_counts": dict(Counter(r["lane"] for r in ordered)),
        "pending_random_windows": sum(r["cohort"] == "PENDING_RANDOM_REVIEW" for r in ordered),
        "source_truth_read": False,
        "protected_evaluation_opened": False,
        "authoritative_ledger_modified": False,
        "selection_policy": (
            "Retained independent random stratum; family round robin within separate lanes. "
            "Signals do not change inclusion or manufacture labels."
        ),
        "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "selection_code_sha256": sha256(Path(__file__)),
        "omission_code_sha256": sha256(Path(__file__).with_name("omission_signals.py")),
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "manifest.json", manifest)
    return manifest
