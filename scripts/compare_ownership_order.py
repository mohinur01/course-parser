# Compare course-text ownership and reading-order methods on the same reviewed pages. Save
# each method's predictions before measuring them against source answers.
"""Seal identical-cohort engine predictions before scoring source-reviewed answers."""

from __future__ import annotations

import argparse
import html
from collections import Counter
from pathlib import Path

from course_parser.entry_learning import compare_learning_runs
from course_parser.io import read_json, read_jsonl, sha256, write_json
from course_parser.learning import run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject


# Read the command-line options, then compare course-text ownership and reading-order methods
# on the same reviewed pages.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--docling-exports", type=Path, required=True)
    parser.add_argument("--ppstructure-exports", type=Path)
    parser.add_argument("--ppstructure-small-exports", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference = ReferenceProject(args.reference_project)
    runs = {"previous_v1": args.before}
    engines = {
        "legacy": ("legacy", None),
        "source_rows": ("source_rows", None),
        "docling": ("docling", args.docling_exports),
    }
    unavailable = {}
    if args.ppstructure_exports:
        pp = read_json(args.ppstructure_exports / "manifest.json")
        if pp.get("status") == "ENGINE_UNAVAILABLE":
            unavailable["ppstructure"] = pp
        else:
            engines["ppstructure"] = ("ppstructure", args.ppstructure_exports)
    if args.ppstructure_small_exports:
        engines["ppstructure_small"] = ("ppstructure", args.ppstructure_small_exports)
    # No scoring/truth import occurs until every source-only prediction is sealed.
    for name, (mode, exports) in engines.items():
        runs[name] = args.output / name / "predictions"
        print(
            name,
            run_learning_baseline(
                reference,
                args.pilot,
                runs[name],
                contextual_course_assembly=True,
                ordering_engine=mode,
                engine_exports=exports,
            ),
            flush=True,
        )
    write_json(
        args.output / "predictions_sealed.json",
        {
            "source_truth_read": False,
            "prediction_manifest_sha256": {
                name: sha256(path / "prediction_manifest.json") for name, path in runs.items()
            },
        },
    )
    summaries = {}
    for name, predictions in runs.items():
        evaluation = args.output / name / "evaluation"
        score_learning(reference, args.pilot, predictions, evaluation)
        summary = read_json(evaluation / "evaluation_manifest.json")
        layouts = [
            layout
            for p in read_jsonl(predictions / "page_proposals.jsonl")
            for layout in p.get("layouts", [])
        ]
        failures = read_jsonl(predictions / "stage_failures.jsonl")
        reviewed_ids = {
            p["dataset_page_id"]
            for p in read_jsonl(args.pilot / "selected_pages.jsonl")
            if p["review_available"]
        }
        summaries[name] = dict(
            overall=summary["overall"],
            by_partition=summary["by_partition"],
            errors=summary["error_counts"],
            layout_status_counts=dict(Counter(layout["status"] for layout in layouts)),
            failed_reviewed_windows=sum(f["dataset_page_id"] in reviewed_ids for f in failures),
            failed_unreviewed_windows=sum(
                f["dataset_page_id"] not in reviewed_ids for f in failures
            ),
            prediction_manifest_sha256=sha256(predictions / "prediction_manifest.json"),
            evaluation_manifest_sha256=sha256(evaluation / "evaluation_manifest.json"),
            evaluation_code_sha256=summary["evaluation_code_sha256"],
        )
        print(name, summary["overall"]["complete_content_correct"], flush=True)
    for name in runs:
        if name != "source_rows":
            compare_learning_runs(
                args.output / name / "evaluation",
                args.output / "source_rows/evaluation",
                args.output / (name + "-vs-source_rows"),
            )
    result = dict(
        status="PAIRED_DEVELOPMENT_ENGINE_COMPARISON",
        runs=summaries,
        unavailable=unavailable,
        source_truth_read_during_prediction=False,
        protected_evaluation_opened=False,
        engine_comparison_scope=(
            "External region order projected to identical native cells/roles "
            "and v2 ownership; not end-to-end engine OCR accuracy."
        ),
        default_engine="source_rows",
        engine_configurations={
            name: read_json(exports / "manifest.json")["settings"]
            for name, (_, exports) in engines.items()
            if exports
        },
        promotion_status="NOT_APPROVED_FOR_PRODUCTION",
    )
    write_json(args.output / "comparison.json", result)
    labels = {
        "previous_v1": "Previous parser",
        "legacy": "Improved ownership + previous order",
        "source_rows": "Improved ownership + source rows",
        "docling": "Docling region order + native rows",
        "ppstructure": "PP default detector + PP XY-cut + native rows",
        "ppstructure_small": "PP-DocLayout-S + PP XY-cut + native rows",
    }
    rows = []
    for name, s in summaries.items():
        m = s["overall"]
        rows.append(
            f'<tr><td><a href="{name}/evaluation/">{html.escape(labels[name])}</a></td>'
            f"<td>{m['matched_course_proposals']}/{m['gold_courses']}</td>"
            f"<td>{m['complete_content_correct']}/{m['complete_content_eligible']}</td>"
            f"<td>{m['complete_all_fields_correct']}/{m['complete_all_fields_eligible']}</td>"
            f"<td>{m['false_course_proposals']}</td><td>{m['ambiguous_course_proposals']}</td>"
            f"<td>{s['failed_reviewed_windows']}</td></tr>"
        )
    viewer = f"""<!doctype html><meta charset="utf-8">
    <title>Course ownership and ordering comparison</title>
    <style>body{{font:17px system-ui;max-width:1200px;margin:40px auto;
    padding:0 20px;line-height:1.5}}
    td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}
    table{{border-collapse:collapse;width:100%}}
    @media(max-width:900px){{td,th{{padding:6px;font-size:14px}}}}
    a{{color:#2157a1}}.note{{background:#fff3d9;padding:18px}}img{{max-width:100%}}</style>
    <h1>Course ownership and reading order</h1><p class="note">Same 20 reviewed DEVELOPMENT windows,
    52 courses and unchanged scorer. All predictions sealed before answers were imported.
    This is an exposed diagnostic cohort, not independent production accuracy.</p>
    <table><tr><th>Pipeline</th><th>Located</th><th>Complete core content</th>
    <th>Complete all fields</th>
    <th>False</th><th>Ambiguous</th><th>Failed reviewed windows</th></tr>{"".join(rows)}</table>
    <p>Core content jointly requires exact printed code, title, description
    and confirmed exact block,
    pages and continuation. All fields also requires separately correct credits/prerequisites.
    Unrecovered source absence earns no credit. Failures and abstentions remain misses.</p>
    <p>External engines supply region order; native text and roles, row sorting and course ownership
    are common. Docling uses PDF windows with OCR/table extraction disabled. PP-Structure uses the
    frozen 100 dpi images with the recorded PP detector and official PP XY-cut using native text.
    These results
    compare the recorded configurations and integration, not universal engine performance.</p>
    <p><a href="previous_v1-vs-source_rows/">Inspect before/after source images and fields</a> ·
    <a href="legacy-vs-source_rows/">Isolate the ordering change</a> ·
    <a href="comparison.json">Metrics, partitions and failures</a></p>
    <p>The frozen vocabulary and original hit dispositions remain unchanged;
    keyword ownership accuracy
    is unmeasured until those hits are reviewed. Protected answers were not opened.</p>"""
    (args.output / "index.html").write_text(viewer, encoding="utf-8")
    print("Viewer:", (args.output / "index.html").resolve(), flush=True)


if __name__ == "__main__":
    main()
