# Purpose: Report source-row preservation and optional source-bound order disagreements.
# ruff: noqa: E501 -- Self-contained HTML report and explicit policy prose.
"""Report source-row preservation and optional source-bound order disagreements."""

from __future__ import annotations

import argparse
import difflib
import html
from collections import Counter
from pathlib import Path

from course_parser.field_scoring import enforce_policy
from course_parser.io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
)

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE = WORKSPACE / "artifacts/continuation-phase8-v1/candidate-v1"
HISTORICAL = WORKSPACE / "artifacts/ownership-order-v2/release-full"
EXPOSED_MATRIX = WORKSPACE / "artifacts/validation-transfer-v1/audit"
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
def summarize(name, candidate, output):
    baseline = BASELINE / (name + "-evaluation")
    enforce_policy(baseline, output)
    before = read_json(baseline / "evaluation_manifest.json")
    current = candidate / (name + "-evaluation")
    after = read_json(current / "evaluation_manifest.json")
    # Stop if the candidate does not identify the approved exposed pilot.
    if before["pilot_manifest_sha256"] != after["pilot_manifest_sha256"]:
        raise ValueError("candidate does not identify the approved exposed pilot")
    for evaluation, manifest in ((baseline, before), (current, after)):
        verify_files(evaluation, manifest["file_sha256"])
        prediction = evaluation.parent / (name + "-predictions")
        pm = read_json(prediction / "prediction_manifest.json")
        verify_files(prediction, pm["file_sha256"])
        # Stop if the prediction or strict scoring seal changed.
        if sha256(prediction / "prediction_manifest.json") != manifest[
            "prediction_manifest_sha256"
        ] or manifest["evaluation_code_sha256"] != sha256(
            WORKSPACE / "src/course_parser/learning_evaluation.py"
        ):
            raise ValueError("prediction or strict scoring seal changed")
        # Stop if the primary native ordering changed.
        if pm["ordering_engine"] != "source_rows":
            raise ValueError("primary native ordering changed")
    old = read_jsonl(BASELINE / (name + "-predictions/course_proposals.jsonl"))
    new = read_jsonl(candidate / (name + "-predictions/course_proposals.jsonl"))

    # Remove record-version bookkeeping when comparing the actual saved content.
    def content(records):
        return [{k: v for k, v in r.items() if not k.endswith("_version")} for r in records]

    return {
        "before": {k: before["overall"][k] for k in METRICS},
        "after": {k: after["overall"][k] for k in METRICS},
        "eligible": {
            k: after["overall"][k]
            for k in ("complete_content_eligible", "complete_all_fields_eligible")
        },
        "native_course_records_identical": content(old) == content(new),
        "baseline_manifest_sha256": sha256(baseline / "evaluation_manifest.json"),
        "candidate_manifest_sha256": sha256(current / "evaluation_manifest.json"),
    }


# Read the command-line options, then report whether native text was preserved and where
# optional reading-order tools disagree.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    enforce_policy(BASELINE / "expanded-evaluation", args.output)
    root = args.output.resolve()
    enforce_policy(BASELINE / "expanded-evaluation", root)
    candidate = root / "candidate-v2"
    freeze = read_json(candidate / "source_only_freeze.json")
    # Require the saved source-only snapshot before building this report.
    if freeze["status"] != "BOTH_PREDICTIONS_SEALED_BEFORE_SCORING":
        raise ValueError("source-only seal missing")
    for path, digest in freeze["parser_sha256"].items():
        # Stop if the parser changed after both source runs froze.
        if sha256(WORKSPACE / path) != digest:
            raise ValueError("parser changed after both source runs froze")
    evidence = candidate / "order-evidence"
    # Stop if the second opinions changed after source-only seal.
    if sha256(evidence / "audit_manifest.json") != freeze["order_evidence_manifest_sha256"]:
        raise ValueError("second opinions changed after source-only seal")
    audit = read_json(evidence / "audit_manifest.json")
    verify_files(evidence, audit["files_sha256"])
    # The engine comparison must retain all original observations in its totals.
    if sum(v["reports"] for v in audit["engines"].values()) != audit["reports"]:
        raise ValueError("engine comparison counts lost source observations")
    # Stop if the audit producer changed after source-only seal.
    if sha256(Path(__file__).with_name("audit_phase9_order.py")) != freeze["audit_producer_sha256"]:
        raise ValueError("audit producer changed after source-only seal")
    results = {name: summarize(name, candidate, root) for name in ("original", "expanded")}
    write_json(root / "outcomes.json", results)
    historical = read_json(HISTORICAL / "comparison.json")
    old_results = {}
    for name in ("source_rows", "docling", "ppstructure"):
        value = historical["runs"][name]
        pm_path = HISTORICAL / name / "predictions/prediction_manifest.json"
        # Stop if the historical prediction seal changed.
        if sha256(pm_path) != value["prediction_manifest_sha256"]:
            raise ValueError("historical prediction seal changed")
        old_results[name] = {
            k: value["overall"][k]
            for k in ("gold_courses", "complete_content_correct", "complete_content_eligible")
        }
    # Only this already exposed matrix is read. Reservation identities/answers and the former
    # reservation manifest hash are neither opened nor copied.
    matrix_manifest = read_json(EXPOSED_MATRIX / "audit_manifest.json")
    matrix_path = EXPOSED_MATRIX / "course_failure_matrix_52.jsonl"
    # Stop if the existing source audit matrix changed.
    if sha256(matrix_path) != matrix_manifest["file_sha256"]["course_failure_matrix_52.jsonl"]:
        raise ValueError("existing source audit matrix changed")
    matrix = read_jsonl(matrix_path)
    reading_checks = dict(Counter(r["checks"]["reading_order"] for r in matrix))
    reports = read_jsonl(evidence / "order_diagnostics.jsonl")
    tasks = read_jsonl(evidence / "review_tasks.jsonl")
    rows = []
    for name, result in results.items():
        trs = "".join(
            f"<tr><td>{html.escape(k.replace('_', ' '))}</td><td>{result['before'][k]}</td><td>{result['after'][k]}</td></tr>"
            for k in METRICS
        )
        rows.append(
            f"<section><h2>{name.title()} exposed DEVELOPMENT</h2><table><tr><th>Metric</th><th>Phase 8</th><th>Phase 9</th></tr>{trs}</table><p>Exact course source records unchanged: <strong>{result['native_course_records_identical']}</strong>.</p></section>"
        )
    engines = "".join(
        f"<tr><td>{name}</td><td>{r['reports']}</td><td>{r['flags'].get('REVIEW_ORDER_DISAGREEMENT', 0)}</td><td>{r['flags'].get('REVIEW_EXTERNAL_ORDER_COVERAGE', 0)}</td><td>{r['relation_counts'].get('comparable_pairs', 0)}</td><td>{r['relation_counts'].get('reversed_pairs', 0)}</td></tr>"
        for name, r in audit["engines"].items()
    )
    details = []
    for task in sorted(
        tasks,
        key=lambda t: (
            "REVIEW_ORDER_DISAGREEMENT" not in t["review_flags"],
            -t["counts"]["reversed_pairs"],
            t["task_id"],
        ),
    ):
        report = reports[task["report_index"]]
        cells = {r["region_id"]: r for r in report["affected_source_regions"]}
        trs = "".join(
            f"<tr><td>{html.escape(cells[p['native_before']]['raw_text'])}<br><small>{html.escape(p['native_before'])}</small></td><td>{html.escape(cells[p['native_after']]['raw_text'])}<br><small>{html.escape(p['native_after'])}</small></td></tr>"
            for p in report["reversals"][:30]
        )
        link = f"candidate-v2/original-evaluation/pages/{task['window_id']}/index.html"
        title = f"{task['engine']} · physical page {task['physical_page']} · {task['task_id']}"
        details.append(
            f"<details data-search='{html.escape((title + ' ' + str(task['review_flags'])).casefold(), quote=True)}'><summary>{html.escape(title)} — {html.escape(', '.join(task['review_flags']))}</summary><p><a href='{link}'>Open permitted source window and neighboring pages</a></p><p>Pending source review. Comparable source pairs: {report['counts']['comparable_pairs']}; reversed pairs: {report['counts']['reversed_pairs']}; externally unresolved pairs: {report['counts']['alternative_unresolved_pairs']}. Native/external layout status: {report['native_status']} / {report['alternative_status']}.</p><p>A → B below is native order; the external opinion asserts B → A. These are review questions, not certified native errors. Showing up to 30 pairs; the full diagnostic preserves every reversal and exact source anchor.</p><div class='scroll'><table><tr><th>Native before / external after</th><th>Native after / external before</th></tr>{trs}</table></div></details>"
        )
    document = (
        """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Phase 9 source order</title><style>body{font:17px/1.55 system-ui;background:#f4f7f8;color:#183342;margin:0}main{max-width:1250px;margin:auto;padding:36px}h1{font-size:34px;line-height:1.2}section,details,.notice{background:white;border:1px solid #d5e2e8;border-radius:10px;padding:20px;margin:18px 0}.notice{border-left:6px solid #377381}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;border-bottom:1px solid #dce5e8;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#e6eef1}summary{cursor:pointer;font-weight:650}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.scroll{overflow:auto}input{box-sizing:border-box;width:100%;padding:12px;font:inherit}a{color:#07527a}small{color:#4c626c}[hidden]{display:none}@media(max-width:800px){main{padding:18px}.pair{display:block}}</style><main><h1>Phase 9 — Keep native rows; review competing order</h1><div class='notice'><strong>Implemented optional order review; extraction accuracy unchanged.</strong><p>Complete core extraction remains 35/52 on the original exposed cohort and 66/128 on the expanded cohort, which includes the original. Zero known false-course proposals in both. The reserved 34-window cohort remains sealed until all 18 phases are complete and the parser is frozen.</p></div><h2>How accurate were the suggestions?</h2><p>Keeping native source rows is supported by our earlier controlled comparison and is already the default contextual path. The screenshot's 25/52 native, 9/52 Docling and 0/52 PP results are accurate historical measurements of configured hybrid integrations. They are not a current benchmark or a universal ranking of those libraries. Native extraction now reaches 35/52; external complete-course accuracy was not rerun in this phase.</p><p>The earlier 52-course source audit records reading order PASS for every course, after the mixed-height credit-row and disconnected-cell fixes. That audit is exposed diagnostic evidence and does not establish absence of errors in the wider collection. No remaining source-demonstrated order failure justified changing native order in this phase.</p><h2>What we implemented</h2><p>Cached Docling and PP layouts are projected onto exactly the same current native cells, roles, geometry and evidence anchors. Native and alternative graphs are preserved separately. The audit compares source-pair ordering relations through confirmed graph edges. It never crosses an unresolved edge to invent a sequence. Reversed pairs route REVIEW_ORDER_DISAGREEMENT; uncovered or ambiguous ordering routes a separate coverage review. Missing exports are unavailable evidence, not source negatives.</p><p>Every flagged page has a stable pending review question with exact source anchors and conflicting relations. All flags remain blank for source adjudication. Engine agreement cannot certify a course; neither a review flag nor a high reversal fraction automatically chooses an external order.</p><p>This relation-based comparison follows the distinction between a sequence and richer ordering relations discussed in <a href='https://aclanthology.org/2024.emnlp-main.540/'>Zhang et al., EMNLP 2024</a>. Preserving source-cell coverage separately from cluster assignments also follows <a href='https://github.com/docling-project/docling/blob/main/docling/utils/layout_postprocessor.py'>Docling's layout postprocessor</a>. These are design precedents, not proof of accuracy gains on our catalogs.</p><div class='pair'>"""
        + "".join(rows)
        + f"""</div><h2>Optional evidence on the same 60 permitted source pages</h2><p>{audit["reports"]} comparisons; {audit["unique_review_tasks"]} unique pending review questions. {audit["unavailable_exports"]} unavailable window-page-engine combinations cover the original pilot's pending windows. Cached source-only models were reused; no new inference or Surya installation was performed.</p><div class='scroll'><table><tr><th>Engine</th><th>Page comparisons</th><th>Disagreement flags</th><th>Coverage flags</th><th>Comparable pairs</th><th>Reversed pairs</th></tr>{engines}</table></div><p>Flags can overlap. Pair counts are conditional on both opinions asserting a relation; incomplete coverage is counted separately. These are not course accuracy measurements.</p><h2>A permitted source spot-check</h2><p>One disagreement on <a href='candidate-v2/original-evaluation/pages/v3page_104b2ed7c18622da7036/index.html'>physical page 148 of an exposed diagnostic window</a> concerns a degree-requirement list. The printed ARTH 2301 and COMM 2301 codes are vertically centered alongside two-line International Cinema titles. The native stream puts the first title line before its code; the external opinion puts the code first. This is a list-row linearization difference, not evidence of an independent-course extraction failure. The native table-preservation guard leaves pages containing table cells in their existing order. A future row-aware repair needs explicit grid/row evidence and regression checks; blindly removing that guard could disrupt table ownership. The review queue remains unadjudicated. <a href='permitted_source_spotcheck.json'>Source and image hashes</a>.</p><h2>Pending source-order review explorer</h2><input id='filter' aria-label='Filter order review tasks' placeholder='Search engine, physical page, flag or task ID'><p id='count'></p>"""
        + "".join(details)
        + """<h2>Reproducibility</h2><p><a href='outcomes.json'>Paired native extraction metrics</a> · <a href='candidate-v2/order-evidence/audit_manifest.json'>Order audit summary</a> · <a href='candidate-v2/order-evidence/review_tasks.jsonl'>Blank review queue</a> · <a href='candidate-v2/order-evidence/order_diagnostics.jsonl'>Every relation and source anchor</a> · <a href='candidate-v2/order-evidence/order_opinions.jsonl'>Separate alternative graphs</a> · <a href='candidate-v2/source_only_freeze.json'>Source-only freeze</a> · <a href='reading_order_changes.diff'>Changes</a> · <a href='verification.json'>Verification</a></p></main><script>const rows=[...document.querySelectorAll('details')],f=document.querySelector('#filter');function filter(){let n=0;for(const r of rows){r.hidden=!r.dataset.search.includes(f.value.toLowerCase());if(!r.hidden)n++}document.querySelector('#count').textContent=n+' pending review tasks shown'}f.addEventListener('input',filter);filter();</script></html>"""
    )
    (root / "index.html").write_text(document)
    changes = []
    for path in (
        "src/course_parser/order_audit.py",
        "src/course_parser/learning.py",
        "src/course_parser/__init__.py",
        "pyproject.toml",
        "config/development_phase_execution_v1.json",
        "tests/test_order_audit.py",
        "scripts/audit_phase9_order.py",
        "scripts/measure_phase9_order.py",
        "scripts/build_phase9_order_report.py",
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
    (root / "reading_order_changes.diff").write_text("".join(changes))
    write_json(
        root / "phase9_measurement_manifest.json",
        {
            "status": "OPTIONAL_ORDER_AUDIT_NO_EXTRACTION_GAIN",
            "completed_phase": 9,
            "all_18_phases_complete": False,
            "reserved_cohort_access": "SEALED",
            "source_only_freeze": freeze,
            "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
            "native_order_changed": False,
            "automatic_engine_selection": False,
            "new_truth_labels": 0,
            "outcomes": results,
            "optional_audit": audit,
            "historical_comparison": old_results,
            "exposed_52_course_reading_order_checks": reading_checks,
            "files_sha256": artifact_hashes(root),
        },
    )


if __name__ == "__main__":
    main()
