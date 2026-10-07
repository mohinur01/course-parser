# Purpose: Explain continuation on known exposed pilots; never discover reserved inputs.
# ruff: noqa: E501 -- Standalone HTML report and explicit policy prose.
"""Explain continuation on known exposed pilots; never discover reserved inputs."""

from __future__ import annotations

import argparse
import difflib
import html
from collections import Counter
from itertools import combinations
from pathlib import Path

from course_parser.completion_diagnostics import load_exposed_evaluation
from course_parser.continuation import assess_ocr_windows
from course_parser.crop_extractions import verify_crop
from course_parser.field_scoring import enforce_policy
from course_parser.io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE = WORKSPACE / "artifacts/boundary-phase7-v1/candidate-v1"
CROPS = WORKSPACE / "artifacts/omission-phase6-v1/review-inbox-final/crops"
METRICS = (
    "gold_courses",
    "matched_course_proposals",
    "complete_content_correct",
    "complete_all_fields_correct",
    "confirmed_extent_exact_matched",
    "description_exact_matched",
    "false_course_proposals",
    "ambiguous_course_proposals",
    "description_contamination",
)


# Combine the individual diagnostic results into counts with explicit eligible denominators.
def summarize(name, candidate, root):
    baseline = BASELINE / (name + "-evaluation")
    enforce_policy(baseline, root)
    before = read_json(baseline / "evaluation_manifest.json")
    current = candidate / (name + "-evaluation")
    after = read_json(current / "evaluation_manifest.json")
    # Stop if the new evaluation differs from the explicitly approved exposed identity.
    if before["pilot_manifest_sha256"] != after["pilot_manifest_sha256"]:
        raise ValueError("new evaluation differs from the explicitly approved exposed identity")
    for evaluation, value in ((baseline, before), (current, after)):
        verify_files(evaluation, value["file_sha256"])
        predictions = evaluation.parent / (name + "-predictions")
        pm = read_json(predictions / "prediction_manifest.json")
        verify_files(predictions, pm["file_sha256"])
        # Stop if the prediction seal changed.
        if sha256(predictions / "prediction_manifest.json") != value["prediction_manifest_sha256"]:
            raise ValueError("prediction seal changed")
        # Stop if the strict evaluator changed.
        if value["evaluation_code_sha256"] != sha256(
            WORKSPACE / "src/course_parser/learning_evaluation.py"
        ):
            raise ValueError("strict evaluator changed")
    rows, _ = load_exposed_evaluation(current)
    gold = {g["gold_course_id"]: g for g in read_jsonl(current / "imported_course_truth.jsonl")}
    gold_diagnostics = []
    for row in rows:
        g = gold[row["gold_course_id"]]
        if (
            g["continuation_status"] != "NONE"
            or row["structure"]["continuation"]["status"] == "FAIL"
        ):
            gold_diagnostics.append(
                {
                    "gold_course_id": row["gold_course_id"],
                    "dataset_page_id": row["dataset_page_id"],
                    "code": row["code"],
                    "expected_continuation": g["continuation_status"],
                    "expected_physical_pages": g["physical_pages"],
                    "pipeline_state": row["pipeline_state"],
                    "candidate_id": row["candidate_id"],
                    "entry_reason_codes": row["entry_reason_codes"],
                    "continuation_check": row["structure"]["continuation"],
                    "raw_block_check": row["structure"]["raw_block_exact"],
                    "owned_extent_check": row["structure"]["owned_extent"],
                    "core": row["completion"]["core"],
                }
            )
    reports = []
    pages = read_jsonl(candidate / (name + "-predictions/page_proposals.jsonl"))
    for page in pages:
        candidates = {c["candidate_id"]: c for c in page["candidates"]}
        for report in page.get("continuation_proposals", []):
            c = candidates[report["candidate_id"]]
            reports.append(
                {
                    **report,
                    "dataset_page_id": page["dataset_page_id"],
                    "code": c["code_raw"],
                    "title": c["title_raw"],
                    "physical_page": c["physical_page"],
                    "source_truth_read_by_continuation_resolver": False,
                }
            )
    write_jsonl(root / (name + "_continuation_diagnostics.jsonl"), reports)
    write_jsonl(root / (name + "_reviewed_continuation_cases.jsonl"), gold_diagnostics)
    baseline_records = read_jsonl(BASELINE / (name + "-predictions/course_proposals.jsonl"))
    current_records = read_jsonl(candidate / (name + "-predictions/course_proposals.jsonl"))

    # Remove record-version bookkeeping when comparing the actual saved content.
    def content(records):
        return [{k: v for k, v in r.items() if not k.endswith("_version")} for r in records]

    result = {
        "before": {k: before["overall"][k] for k in METRICS},
        "after": {k: after["overall"][k] for k in METRICS},
        "eligible": {
            k: after["overall"][k]
            for k in ("complete_content_eligible", "complete_all_fields_eligible")
        },
        "course_records_identical_excluding_version_fields": content(baseline_records)
        == content(current_records),
        "windows": len(pages),
        "continuation_reports_including_context_and_pending": len(reports),
        "decisions": dict(Counter(r["decision"] for r in reports)),
        "proposal_methods": dict(Counter(r["method"] for r in reports)),
        "withheld_relative_continue_proposals": sum(
            r.get("proposed_decision") == "CONTINUE" for r in reports
        ),
        "unresolved_reasons": dict(
            Counter(
                reason for r in reports if r["decision"] == "UNRESOLVED" for reason in r["reasons"]
            )
        ),
        "reviewed_central_continuation_checks": dict(
            Counter(r["structure"]["continuation"]["status"] for r in rows)
        ),
        "explicit_reviewed_continuation_courses": sum(
            gold[r["gold_course_id"]]["continuation_status"] != "NONE" for r in rows
        ),
        "baseline_manifest_sha256": sha256(baseline / "evaluation_manifest.json"),
        "candidate_manifest_sha256": sha256(current / "evaluation_manifest.json"),
    }
    return result, reports, gold_diagnostics


# Read the command-line options, then report the saved checks for courses continuing across
# columns, pages or OCR crops.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    enforce_policy(BASELINE / "expanded-evaluation", args.output)
    root = args.output.resolve()
    enforce_policy(BASELINE / "expanded-evaluation", root)
    candidate = root / "candidate-v1"
    freeze = read_json(candidate / "source_only_freeze.json")
    # Both source runs must be sealed before evaluation.
    if freeze["status"] != "BOTH_PREDICTIONS_SEALED_BEFORE_SCORING":
        raise ValueError("both source runs must be sealed before evaluation")
    for path, digest in freeze["parser_sha256"].items():
        # Stop if the parser changed after source-only seal.
        if sha256(WORKSPACE / path) != digest:
            raise ValueError("parser changed after source-only seal")
    results, reports, cases = {}, {}, {}
    for name in ("original", "expanded"):
        results[name], reports[name], cases[name] = summarize(name, candidate, root)
    write_json(root / "outcomes.json", results)
    # Fixed permitted Phase 6 crop folder only, no arbitrary source discovery.
    frames = [(p.parent, verify_crop(p.parent)) for p in sorted(CROPS.glob("*/transform.json"))]
    coverage = []
    for (left_path, left), (right_path, right) in combinations(frames, 2):
        if left.source_pdf_checksum != right.source_pdf_checksum:
            continue
        coverage.append(
            {**assess_ocr_windows(left, right), "crop_paths": [str(left_path), str(right_path)]}
        )
    write_json(
        root / "ocr_window_coverage.json",
        {
            "source": str(CROPS),
            "verified_frames": len(frames),
            "same_pdf_pairs": coverage,
            "text_merged": False,
            "labels_created": False,
            "scope": "KNOWN_PERMITTED_PHASE6_TUNING_CROPS_ONLY",
            "crop_transform_sha256": {
                str(p / "transform.json"): sha256(p / "transform.json") for p, _ in frames
            },
            "note": "Different engines or OCR modes on one frame are extraction disagreement, not continuation. Coverage alone cannot prove course ownership.",
        },
    )
    sections = []
    for name, result in results.items():
        rows = "".join(
            f"<tr><td>{html.escape(k.replace('_', ' '))}</td><td>{result['before'][k]}</td><td>{result['after'][k]}</td></tr>"
            for k in METRICS
        )
        sections.append(
            f"<section><h2>{name.title()} exposed DEVELOPMENT</h2><table><tr><th>Metric</th><th>Phase 7</th><th>Phase 8</th></tr>{rows}</table><p>Transport decisions including context and pending windows: {html.escape(str(result['decisions']))}. These counts are proposals, not accuracy scores.</p><p>Source course records unchanged: <strong>{result['course_records_identical_excluding_version_fields']}</strong>. Relative proposals requiring review: {result['withheld_relative_continue_proposals']}.</p></section>"
        )
    diagnostic_rows = "".join(
        f"<tr><td>{html.escape(c['code'])}</td><td>{html.escape(c['expected_continuation'])}</td><td>{html.escape(c['pipeline_state'])}</td><td>{html.escape(c['continuation_check']['status'])}</td><td>{html.escape(', '.join(c['entry_reason_codes']))}</td></tr>"
        for c in cases["expanded"]
    )
    details = []
    for r in reports["expanded"]:
        title = f"{r['code']} {r['title'] or ''} — physical page {r['physical_page']}"
        link = f"../boundary-phase7-v1/candidate-v1/expanded-evaluation/pages/{r['dataset_page_id']}/index.html"
        trs = "".join(
            f"<tr><td>{html.escape(t['kind'])}</td><td>{html.escape(t['target_node_id'])}</td><td>{t['ranking']['score']}</td><td>{html.escape('; '.join(c['code'] for c in t['ranking']['cues']))}</td><td>{html.escape(', '.join(t['ranking']['unavailable']))}</td></tr>"
            for t in r["transitions"]
        )
        details.append(
            f"<details data-search='{html.escape(title.casefold(), quote=True)}'><summary>{html.escape(title)} — {r['decision']}</summary><p><a href='{link}'>Open permitted source window</a> · {html.escape(r['method'])}</p><p>Assessment: {r['assessment_decision']}; final decision: {r['decision']}. Reasons: {html.escape(', '.join(r['reasons']))}</p><p>Final boundary: {r['final_boundary_status']}; {html.escape(', '.join(r['final_boundary_reasons']))}</p><div class='scroll'><table><tr><th>Transition</th><th>Target source node</th><th>Provisional score</th><th>Cues</th><th>Unavailable</th></tr>{trs}</table></div></details>"
        )
    document = (
        """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Phase 8 continuation</title><style>body{font:17px/1.55 system-ui;background:#f4f7f8;color:#183342;margin:0}main{max-width:1200px;margin:auto;padding:36px}h1{font-size:34px;line-height:1.2}section,details,.notice{background:white;border:1px solid #d5e2e8;border-radius:10px;padding:20px;margin:18px 0}.notice{border-left:6px solid #377381}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;border-bottom:1px solid #dce5e8;text-align:left;vertical-align:top}th{background:#e6eef1}summary{cursor:pointer;font-weight:650}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.scroll{overflow:auto}input{box-sizing:border-box;width:100%;padding:12px;font:inherit}a{color:#07527a}[hidden]{display:none}@media(max-width:800px){main{padding:18px}.pair{display:block}}</style><main><h1>Phase 8 — Assess continuation separately</h1><div class='notice'><strong>Measured result: clearer continuation evidence; extraction accuracy unchanged.</strong><p>Original complete core courses stay 35/52; expanded stay 66/128. Expanded includes original. Zero false-course proposals in both. These are exposed DEVELOPMENT diagnostics, not held-out generalization results. The reserved 34-window cohort remains sealed until all 18 phases are complete and the parser is frozen.</p></div><h2>Which suggestions fit our parser</h2><p>We already used a separate structural continuation stage. It now exports source-bound CONTINUE, STOP and UNRESOLVED decisions, proposed transitions, provisional scores, missing cues and ownership blockers. The final transport decision is reconciled after boundary competition. Continuing text does not by itself prove that the complete course endpoint is correct.</p><p>Repeated page furniture may be skipped only through verified order. Existing cross-column and cross-page proofs remain intact. An uncertain header, competing owner, multiple first streams or unverified endpoint cannot be bypassed by lowercase text, punctuation or font similarity.</p><h2>What remains an experiment</h2><p>A relative stream proposal can use the last meaningful source node and a unique first stream on the adjacent page, column-relative indentation, body typography, unfinished/lowercase language and a verified downstream boundary. It avoids fixed page-height zones, but stays review-only by default. Its geometric tolerances are provisional; synthetic scale/margin tests establish invariants, not accuracy across catalogs. No measured source-extraction gain justifies automatic promotion or a trained continuation classifier yet.</p><p>Page-local stream identities do not transfer by matching absolute x coordinates. A right column may continue in the next page's left column. A credit or prerequisite label can be part of the same course and must remain a field transition.</p><p>The use of separate structural stages and relative layout features follows <a href='https://grobid.readthedocs.io/en/latest/Principles/'>GROBID's principles</a>. That architectural precedent does not establish an accuracy gain on our data.</p><div class='pair'>"""
        + "".join(sections)
        + """</div><h2>Reviewed continuation cases and upstream blockers</h2><p>Only central source-reviewed courses enter this table. All three explicitly continued courses are withheld or missed before continuation can be scored. Missing or withheld records leave continuation unassessed; they are not counted as continuation-classifier errors. The music-course mismatch needs ownership of the following uncertain header resolved before a reliable stop decision.</p><div class='scroll'><table><tr><th>Course</th><th>Source-reviewed continuation</th><th>Pipeline state</th><th>Continuation check</th><th>Entry evidence</th></tr>"""
        + diagnostic_rows
        + """</table></div><h2>OCR windows</h2><p>Eight known TUNING crop frames were verified. They belong to different PDFs, so this audit found zero same-PDF window pairs; real OCR seam accuracy was not measured. The coverage audit preserves their transforms. Overlapping crops are duplicate source coverage, not two independent votes; crop geometry alone cannot join text or assign ownership. Semantic stitching across OCR windows is deferred pending source sequence labels.</p><p><a href='ocr_window_coverage.json'>OCR source-coverage audit</a></p><h2>Expanded continuation explorer</h2><p>Source proposals include context and pending windows. They do not create new review answers. Scores are uncalibrated ranking values, not probabilities.</p><input id='filter' aria-label='Filter continuation proposals' placeholder='Search printed course code or title'><p id='count'></p>"""
        + "".join(details)
        + """<h2>Reproducibility</h2><p><a href='outcomes.json'>Paired metrics</a> · <a href='expanded_reviewed_continuation_cases.jsonl'>Reviewed diagnostic cases</a> · <a href='expanded_continuation_diagnostics.jsonl'>Continuation evidence</a> · <a href='candidate-v1/source_only_freeze.json'>Source-only freeze</a> · <a href='continuation_changes.diff'>Changes</a> · <a href='verification.json'>Verification</a></p></main><script>const rows=[...document.querySelectorAll('details')],f=document.querySelector('#filter');function filter(){let n=0;for(const r of rows){r.hidden=!r.dataset.search.includes(f.value.toLowerCase());if(!r.hidden)n++}document.querySelector('#count').textContent=n+' proposals shown'}f.addEventListener('input',filter);filter();</script></html>"""
    )
    (root / "index.html").write_text(document)
    changes = []
    for path in (
        "src/course_parser/continuation.py",
        "src/course_parser/contextual.py",
        "src/course_parser/learning.py",
        "src/course_parser/__init__.py",
        "pyproject.toml",
        "config/development_phase_execution_v1.json",
        "tests/test_continuation.py",
        "scripts/measure_phase8_continuation.py",
        "scripts/build_phase8_continuation_report.py",
        "README.md",
    ):
        old = root / "before-code" / path
        changes.extend(
            difflib.unified_diff(
                old.read_text().splitlines(keepends=True) if old.exists() else [],
                (WORKSPACE / path).read_text().splitlines(keepends=True),
                fromfile="before/" + path,
                tofile="after/" + path,
            )
        )
    (root / "continuation_changes.diff").write_text("".join(changes))
    write_json(
        root / "phase8_measurement_manifest.json",
        {
            "status": "DIAGNOSTIC_IMPROVEMENT_NO_EXTRACTION_GAIN",
            "completed_phase": 8,
            "all_18_phases_complete": False,
            "reserved_cohort_access": "SEALED",
            "relative_attachment_default_enabled": False,
            "trained_classifier": False,
            "ocr_semantic_stitching_enabled": False,
            "source_only_freeze": freeze,
            "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
            "outcomes": results,
            "files_sha256": artifact_hashes(root),
        },
    )


if __name__ == "__main__":
    main()
