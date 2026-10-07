# Compare saved predictions before and after table-ownership changes. Require identical source
# reviews so a changed answer key cannot explain a reported gain.
"""Compare sealed old/new predictions with identical reviewed source answers."""

from __future__ import annotations

import argparse
import html
from collections import Counter
from pathlib import Path

from course_parser.entry_learning import compare_learning_runs
from course_parser.expanded_review import build_expanded_review
from course_parser.io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
)
from course_parser.learning import verify_learning_pilot
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject

METRICS = (
    "gold_courses",
    "complete_content_correct",
    "complete_all_fields_correct",
    "confirmed_extent_exact_matched",
    "description_exact_matched",
    "matched_course_proposals",
    "central_course_proposals",
    "false_course_proposals",
    "ambiguous_course_proposals",
    "false_proposals_on_zero_course_pages",
    "parser_failed_pages",
)
STYLE = """<style>body{font:17px system-ui;max-width:1100px;margin:25px auto;padding:16px;
background:#f6f8fb;color:#182535}table{border-collapse:collapse;width:100%;background:white}
td,th{border:1px solid #ccd4dc;padding:8px;text-align:left;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}
.note{padding:16px;background:#fff0bd}img{max-width:100%}a{color:#1256a0}</style>"""


# Format the paired evaluation metrics as an escaped comparison table.
def metric_table(old, new):
    return (
        "<table><tr><th>Source-reviewed result</th><th>Previous</th><th>Revised</th></tr>"
        + "".join(
            f"<tr><td>{key.replace('_', ' ')}</td><td>{old.get(key, 0)}</td>"
            f"<td>{new.get(key, 0)}</td></tr>"
            for key in METRICS
        )
        + "</table>"
    )


# Read the command-line options, then compare saved predictions before and after
# table-ownership changes.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("reference-project", "pilot", "before", "after", "original-pilot", "output"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    verify_learning_pilot(args.pilot)
    sealed = []
    for root in (args.before, args.after):
        manifest = read_json(root / "prediction_manifest.json")
        # Comparison requires source-only sealed predictions.
        if (
            manifest["source_truth_read"] is not False
            or manifest["protected_evaluation_opened"] is not False
        ):
            raise ValueError("comparison requires source-only sealed predictions")
        # Compare old and new predictions only when they describe the same sample.
        if manifest["pilot_manifest_sha256"] != sha256(args.pilot / "pilot_manifest.json"):
            raise ValueError("old/new prediction cohorts differ")
        verify_files(root, manifest["file_sha256"])
        sealed.append(sha256(root / "prediction_manifest.json"))
    write_json(
        args.output / "predictions_sealed_before_answer_import.json",
        {
            "before_prediction_manifest_sha256": sealed[0],
            "after_prediction_manifest_sha256": sealed[1],
            "source_truth_read": False,
            "protected_evaluation_opened": False,
        },
    )
    queue = build_expanded_review(args.pilot, args.after, args.output / "review_selection")
    reference = ReferenceProject(args.reference_project)
    for name, predictions in (("before", args.before), ("after", args.after)):
        result = score_learning(reference, args.pilot, predictions, args.output / name)
        print(name, {key: result["overall"].get(key, 0) for key in METRICS}, flush=True)
    comparison = compare_learning_runs(
        args.output / "before", args.output / "after", args.output / "comparison"
    )
    verify_learning_pilot(args.original_pilot, recheck_inputs=False)
    original = read_jsonl(args.original_pilot / "selected_pages.jsonl")
    selected = {p["dataset_page_id"]: p for p in read_jsonl(args.pilot / "selected_pages.jsonl")}
    for item in original:
        retained = selected[item["dataset_page_id"]]
        # Stop if the retained original page identity or partition changed.
        if any(
            item[k] != retained[k]
            for k in (
                "source_pdf_checksum",
                "central_physical_page",
                "context_physical_pages",
                "learning_partition",
                "learning_group",
            )
        ):
            raise ValueError("retained original page identity or partition changed")
    original_ids = {p["dataset_page_id"] for p in original}
    retained_metrics = {}
    for name in ("before", "after"):
        rows = [
            r
            for r in read_jsonl(args.output / name / "page_metrics.jsonl")
            if r["dataset_page_id"] in original_ids
        ]
        retained_metrics[name] = {key: sum(r.get(key, 0) for r in rows) for key in METRICS}
        retained_metrics[name]["reviewed_pages"] = sum(
            r["status"] == "SCORED_DEVELOPMENT" for r in rows
        )
    write_json(args.output / "retained_original_cohort.json", retained_metrics)
    proposals = read_jsonl(args.after / "page_proposals.jsonl")
    conservation = Counter(
        a["status"] for p in proposals for a in p.get("layout_interpretations", [])
    )
    source_grid_cells = sum(
        r["role"] == "TABLE_CELL"
        for p in proposals
        for lay in p.get("source_layouts", [])
        for r in lay["regions"]
    )
    purposes = Counter(
        info["container"]
        for p in proposals
        for _, info in {
            (v["table_id"], v["row_id"], k.rsplit(":", 1)[0]): v
            for k, v in p.get("table_context_proposals", {}).items()
        }.items()
    )
    audit = {
        "layout_conservation": dict(conservation),
        "archived_source_grid_cells": source_grid_cells,
        "table_purpose_proposals": dict(purposes),
        "retained_original_windows": len(original),
        "original_hits_pending": read_json(args.pilot / "pilot_manifest.json")[
            "original_physical_hits"
        ],
        "keyword_ownership_precision": None,
        "keyword_ownership_recall": None,
        "protected_evaluation_opened": False,
        "authoritative_ledger_modified": False,
    }
    write_json(args.output / "source_ownership_audit.json", audit)
    source_cards = args.output / "source_structure"
    source_cards.mkdir()
    navigation = []
    for p in proposals:
        item = selected[p["dataset_page_id"]]
        pid, page = item["dataset_page_id"], item["central_physical_page"]
        cell_rows = []
        for lay in p.get("source_layouts", []):
            if lay["physical_page"] != page:
                continue
            for region in lay["regions"]:
                if region["table_id"]:
                    cell_rows.append(
                        "<tr>"
                        + "".join(
                            f"<td>{html.escape(str(region[k]))}</td>"
                            for k in ("table_id", "row_id", "cell_id", "raw_text")
                        )
                        + "</tr>"
                    )
        body = (
            f"<!doctype html><meta charset='utf-8'><title>Preserved source cells</title>{STYLE}"
            f"<a href='../'>← Comparison</a><h1>{html.escape(item['institution'])} "
            f"· page {page}</h1>"
            "<p class='note'>Original grid IDs are preserved below. A grid is a layout proposal; "
            "the image establishes whether its rows are courses, references or prose.</p>"
            f"<a href='../after/pages/{pid}/'>Reviewed source window and course proposals</a>"
            f"<details open><summary>Central source image</summary><img alt='Source catalog page' "
            f"src='../after/pages/{pid}/page_{page:05d}.png'></details>"
            "<details><summary>Original cells and rows</summary><table><tr><th>Table</th>"
            "<th>Row</th><th>Cell</th><th>Exact cell text</th></tr>"
            + "".join(cell_rows)
            + "</table></details>"
        )
        (source_cards / f"{pid}.html").write_text(body, encoding="utf-8")
        navigation.append(
            f"<tr><td><a href='source_structure/{pid}.html'>"
            f"{html.escape(item['institution'])}</a></td>"
            f"<td>{page}</td><td>{item['learning_partition']}</td>"
            f"<td>{item['pilot_role']}</td><td>{p['status']}</td></tr>"
        )
    document = (
        f"<!doctype html><meta charset='utf-8'><title>Course ownership comparison</title>{STYLE}"
        "<h1>Course ownership: paired source comparison</h1>"
        "<p class='note'>Same 50 reviewed DEVELOPMENT pages; "
        "18 unreviewed windows excluded from accuracy. "
        "Existing reviews are not independently recertified gold. "
        "Protected evaluation remains sealed.</p>"
        + metric_table(comparison["before"], comparison["after"])
        + "<h2>Retained original 20 reviewed pages</h2>"
        + metric_table(retained_metrics["before"], retained_metrics["after"])
        + "<p><a href='comparison/'>Full paired metrics and source evidence</a> · "
        "<a href='review_selection/review_queue.jsonl'>"
        "Separate random and active review lanes</a> · "
        "<a href='source_ownership_audit.json'>Source conservation audit</a></p>"
        "<h2>Inspect the source and preserved cells</h2>"
        "<table><tr><th>Institution</th><th>Page</th>"
        "<th>Partition</th><th>Review role</th><th>Proposal status</th></tr>"
        + "".join(navigation)
        + "</table>"
    )
    (args.output / "index.html").write_text(document, encoding="utf-8")
    write_json(
        args.output / "measurement_manifest.json",
        {
            "status": "PAIRED_SOURCE_COMPARISON_VERIFIED",
            "before_manifest_sha256": sealed[0],
            "after_manifest_sha256": sealed[1],
            "pilot_manifest_sha256": sha256(args.pilot / "pilot_manifest.json"),
            "review_queue": queue,
            "source_audit": audit,
            "scoring_modules_sha256": {
                str(path): sha256(path)
                for path in Path(__file__)
                .resolve()
                .parents[1]
                .joinpath("src/course_parser")
                .glob("*metrics.py")
            },
            "file_sha256": artifact_hashes(args.output),
            "protected_evaluation_opened": False,
        },
    )


if __name__ == "__main__":
    main()
