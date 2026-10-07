# Compare extraction with and without field-sequence checks. Save predictions first and keep
# the blank annotation audit separate from measured course accuracy.
"""Seal constrained proposals, audit blank labels, then compare reviewed extraction."""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path

from course_parser.entry_learning import compare_learning_runs
from course_parser.io import read_json, read_jsonl, sha256, verify_files, write_json
from course_parser.learning import run_learning_baseline
from course_parser.learning_evaluation import score_learning
from course_parser.reference import ReferenceProject
from course_parser.sequence_annotations import audit_sequence_reviews, export_sequence_templates


# Escape displayed text so it cannot be interpreted as HTML markup.
def esc(value):
    return html.escape(str(value))


STYLE = """<style>body{font:17px system-ui;max-width:1100px;margin:25px auto;padding:16px;
color:#182535;background:#f6f8fb}table{width:100%;border-collapse:collapse;background:white}
td,th{padding:8px;border:1px solid #ccd4dc;text-align:left;overflow-wrap:anywhere}
table{table-layout:fixed}img{max-width:100%}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:12px}
.note{padding:16px;background:#fff0bd}details{margin:12px 0}
@media(max-width:800px){td,th{padding:5px;font-size:14px}}</style>"""


# Write a report showing the competing field sequences and the source text behind them.
def write_constraint_viewer(output, pilot, predictions):
    manifest = read_json(predictions / "prediction_manifest.json")
    # Stop if the constraint viewer source differs from sealed predictions.
    if manifest["pilot_manifest_sha256"] != sha256(pilot / "pilot_manifest.json"):
        raise ValueError("constraint viewer source differs from sealed predictions")
    verify_files(predictions, manifest["file_sha256"])
    selected = {p["dataset_page_id"]: p for p in read_jsonl(pilot / "selected_pages.jsonl")}
    course_map = {
        (r["dataset_page_id"], r["course"]["block_id"]): r["course"]
        for r in read_jsonl(predictions / "course_proposals.jsonl")
    }
    details = output / "constraints"
    # Derived HTML can be refreshed; prediction/evaluation JSON stays sealed.
    details.mkdir(exist_ok=True)
    navigation, field_status, boundary_status = [], Counter(), Counter()
    central_fields, central_boundaries = Counter(), Counter()
    for row in read_jsonl(predictions / "page_proposals.jsonl"):
        pid, cards = row["dataset_page_id"], []
        item = selected[pid]
        central = {
            c["candidate_id"]
            for c in row.get("candidates", [])
            if c["physical_page"] == item["central_physical_page"]
        }
        sequences = {s["block_id"]: s for s in row.get("field_sequence_proposals", [])}
        field_status.update(s["status"] for s in sequences.values())
        boundary_status.update(s["selection_reason"] for s in row.get("boundary_proposals", []))
        central_fields.update(
            s["status"] for s in sequences.values() if s["candidate_id"] in central
        )
        central_boundaries.update(
            s["selection_reason"]
            for s in row.get("boundary_proposals", [])
            if s["candidate_id"] in central
        )
        for b in row.get("boundary_proposals", []):
            if b["candidate_id"] not in central:
                continue
            course = course_map[(pid, b["block_id"])]
            sequence = sequences[b["block_id"]]
            paths = "\n".join(" → ".join(p["roles"]) for p in sequence["paths"])
            table = "".join(
                f"<tr><td>{esc(p['origin'].replace('_', ' ').lower())}</td>"
                f"<td>{esc(p['action'])}</td><td>{esc(p['status'])}</td>"
                f"<td>{esc(', '.join(p['violations']).replace('_', ' ').lower())}</td></tr>"
                for p in b["proposals"]
            )
            cards.append(
                f"<h2>{esc(course['code']['raw'])} · {esc(course['title']['raw'])}</h2>"
                f"<p>Boundary: {esc(b['selection_reason'].replace('_', ' ').lower())}. "
                f"Fields: {esc(sequence['status'].replace('_', ' ').lower())}.</p>"
                f"<details><summary>Competing stop / continue proposals</summary>"
                f"<table><tr><th>Producer</th><th>Action</th><th>Admission</th><th>Reason</th></tr>{table}</table>"
                "<details><summary>Boundary evidence and source IDs</summary>"
                f"<pre>{esc(json.dumps(b, ensure_ascii=False, indent=2))}</pre></details></details>"
                f"<details><summary>Field paths and rejected transitions</summary>"
                f"<p>These rule scores are not probabilities.</p><pre>{esc(paths)}</pre>"
                f"<pre>{esc(json.dumps(sequence, ensure_ascii=False, indent=2))}</pre></details>"
                "<details><summary>Exact proposed source block</summary>"
                f"<pre>{esc(course['raw_course_block'])}</pre></details>"
            )
        image = f"../after/pages/{pid}/page_{item['central_physical_page']:05d}.png"
        document = (
            f"<!doctype html><meta charset='utf-8'><title>Source boundary proposals</title>{STYLE}"
            f"<a href='../'>← Comparison</a><h1>{esc(item['institution'])} "
            f"· page {item['central_physical_page']}</h1>"
            f"<p class='note'>{esc(item['learning_partition'])}. Proposals require review. "
            "Rejected alternatives never attach source text; ambiguous fields stay withheld.</p>"
            f"<a href='../after/pages/{pid}/'>Full source window and reviewed diagnostic</a>"
            "<details><summary>Central source image</summary>"
            f"<img src='{image}' alt='Catalog source page'></details>"
            + ("".join(cards) or "<p>No central-page course ownership proposal.</p>")
        )
        (details / f"{pid}.html").write_text(document, encoding="utf-8")
        navigation.append(
            f"<tr><td><a href='constraints/{pid}.html'>{esc(item['institution'])}</a></td>"
            f"<td>{item['central_physical_page']}</td><td>{esc(item['learning_partition'])}</td>"
            f"<td>{len(cards)}</td></tr>"
        )
    return navigation, {
        "all_context_courses": {
            "field_sequence_status": dict(field_status),
            "boundary_selection": dict(boundary_status),
        },
        "central_courses": {
            "field_sequence_status": dict(central_fields),
            "boundary_selection": dict(central_boundaries),
        },
        "note": "Counts include unreviewed proposals and are not accuracy or error counts.",
    }


# Read the command-line options, then compare extraction with and without field-sequence
# checks.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("reference-project", "pilot", "before", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference = ReferenceProject(args.reference_project)
    predictions = args.output / "predictions"
    print(
        run_learning_baseline(
            reference,
            args.pilot,
            predictions,
            contextual_course_assembly=True,
            ordering_engine="source_rows",
        ),
        flush=True,
    )
    templates = args.output / "sequence_annotations"
    print(export_sequence_templates(args.pilot, predictions, templates), flush=True)
    readiness = audit_sequence_reviews(templates, templates / "sequence_template.jsonl")
    write_json(args.output / "sequence_readiness.json", readiness)
    write_json(
        args.output / "predictions_sealed.json",
        {
            "source_truth_read": False,
            "before_prediction_manifest_sha256": sha256(args.before / "prediction_manifest.json"),
            "after_prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        },
    )
    score_learning(reference, args.pilot, args.before, args.output / "before")
    score_learning(reference, args.pilot, predictions, args.output / "after")
    result = compare_learning_runs(
        args.output / "before", args.output / "after", args.output / "comparison"
    )
    navigation, counts = write_constraint_viewer(args.output, args.pilot, predictions)
    write_json(args.output / "constraint_summary.json", counts)
    metrics = (
        "gold_courses",
        "matched_course_proposals",
        "complete_content_correct",
        "complete_all_fields_correct",
        "confirmed_extent_exact_matched",
        "description_exact_matched",
        "false_course_proposals",
        "ambiguous_course_proposals",
    )
    rows = "".join(
        f"<tr><td>{esc(k.replace('_', ' '))}</td><td>{result['before'][k]}</td>"
        f"<td>{result['after'][k]}</td></tr>"
        for k in metrics
    )
    document = (
        f"<!doctype html><meta charset='utf-8'><title>Course sequence constraints</title>{STYLE}"
        "<h1>Course ownership and boundary constraints</h1>"
        "<p class='note'>Same exposed DEVELOPMENT cohort and unchanged scorer. "
        "This comparison measures current extraction; it does not establish generalization. "
        "Original keyword decisions remain pending.</p>"
        f"<table><tr><th>Result</th><th>Previous</th><th>Constrained</th></tr>{rows}</table>"
        "<p><a href='comparison/'>Full paired result and source views</a> · "
        "<a href='sequence_readiness.json'>Sequence annotation readiness</a> · "
        "<a href='sequence_annotations/sequence_template.jsonl'>Blank TUNING annotations</a></p>"
        "<details><summary>Constraint outcomes — proposal counts, not accuracy</summary>"
        f"<pre>{esc(json.dumps(counts, indent=2))}</pre></details>"
        "<h2>Inspect competing boundary and field proposals</h2>"
        "<table><tr><th>Institution</th><th>Page</th><th>Partition</th>"
        "<th>Course proposals</th></tr>" + "".join(navigation) + "</table>"
    )
    (args.output / "index.html").write_text(document, encoding="utf-8")
    for run in (predictions, args.output / "before", args.output / "after", templates):
        manifest = read_json(
            run
            / (
                "prediction_manifest.json"
                if run == predictions
                else "manifest.json"
                if run == templates
                else "evaluation_manifest.json"
            )
        )
        verify_files(run, manifest["file_sha256"])
    write_json(
        args.output / "verification.json",
        {
            "status": "PASS",
            "source_truth_read_during_prediction": False,
            "protected_evaluation_opened": False,
            "predictions_and_evaluations_verified": True,
            "workspace_code_matches_prediction": all(
                sha256(Path(p)) == h
                for p, h in read_json(predictions / "prediction_manifest.json")[
                    "workspace_code_sha256"
                ].items()
            ),
        },
    )
    print(
        json.dumps(
            {
                "before": result["before"],
                "after": result["after"],
                "constraints": counts,
                "annotation_readiness": readiness,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
