# Purpose: Report provisional boundary evidence on two known exposed DEVELOPMENT pilots.
# ruff: noqa: E501 -- Self-contained HTML report and explicit policy prose.
"""Report provisional boundary evidence on two known exposed DEVELOPMENT pilots."""

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
    write_jsonl,
)

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE = WORKSPACE / "artifacts/historical-detection-phase5-v1/candidate-v1"
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
    before_path = BASELINE / (name + "-evaluation")
    enforce_policy(before_path, output)
    before = read_json(before_path / "evaluation_manifest.json")["overall"]
    current_eval = candidate / (name + "-evaluation")
    # A newly generated evaluation is linked to an approved exposed identity; do not add
    # arbitrary evaluation discovery or arbitrary pilot arguments.
    manifest = read_json(current_eval / "evaluation_manifest.json")
    old_manifest = read_json(before_path / "evaluation_manifest.json")
    for evaluation, value in ((before_path, old_manifest), (current_eval, manifest)):
        verify_files(evaluation, value["file_sha256"])
        predictions = evaluation.parent / (name + "-predictions")
        prediction_manifest = read_json(predictions / "prediction_manifest.json")
        verify_files(predictions, prediction_manifest["file_sha256"])
        # Stop if the evaluation prediction seal changed.
        if value["prediction_manifest_sha256"] != sha256(predictions / "prediction_manifest.json"):
            raise ValueError("evaluation prediction seal changed")
        # Stop if the strict scorer changed.
        if value["evaluation_code_sha256"] != sha256(
            WORKSPACE / "src/course_parser/learning_evaluation.py"
        ):
            raise ValueError("strict scorer changed")
    # Stop if the candidate evaluation is not the approved exposed pilot.
    if manifest["pilot_manifest_sha256"] != old_manifest["pilot_manifest_sha256"]:
        raise ValueError("candidate evaluation is not the approved exposed pilot")
    after = manifest["overall"]
    pages = read_jsonl(candidate / (name + "-predictions") / "page_proposals.jsonl")
    reports = []
    for page in pages:
        candidates = {c["candidate_id"]: c for c in page["candidates"]}
        for report in page["boundary_proposals"]:
            c = candidates[report["candidate_id"]]
            reports.append(
                {
                    **report,
                    "dataset_page_id": page["dataset_page_id"],
                    "physical_page": c["physical_page"],
                    "code": c["code_raw"],
                    "title": c["title_raw"],
                    "proposal_source": name,
                    "source_truth_read_by_boundary_scorer": False,
                }
            )
    write_jsonl(output / (name + "_boundary_diagnostics.jsonl"), reports)
    proposed = [p for r in reports for p in r["proposals"]]
    score_count = sum("ranking" in p for p in proposed)
    selection = Counter(r["selection_reason"] for r in reports)
    groups = Counter(len(r["ranking"]["admissible_endpoint_groups"]) for r in reports)
    baseline_courses = read_jsonl(BASELINE / (name + "-predictions") / "course_proposals.jsonl")
    current_courses = read_jsonl(candidate / (name + "-predictions") / "course_proposals.jsonl")

    # Remove record-version bookkeeping when comparing the actual saved content.
    def content(rows):
        return [
            {key: value for key, value in row.items() if not key.endswith("_version")}
            for row in rows
        ]

    unchanged = content(baseline_courses) == content(current_courses)
    summary = {
        "before": {k: before[k] for k in METRICS},
        "after": {k: after[k] for k in METRICS},
        "eligible": {
            k: after[k] for k in ("complete_content_eligible", "complete_all_fields_eligible")
        },
        "course_records_identical_excluding_version_fields": unchanged,
        "windows": len(pages),
        "boundary_reports_including_context_pages": len(reports),
        "scored_proposals": score_count,
        "selection_reasons": dict(selection),
        "endpoint_group_counts": dict(groups),
        "review_alternatives": dict(
            Counter(
                p["origin"]
                for p in proposed
                if p["origin"]
                in {
                    "PREREQUISITE_IS_FIELD_TRANSITION",
                    "LARGE_RELATIVE_GAP",
                    "COLUMN_CHANGE_IS_NOT_COURSE_END_PROOF",
                }
            )
        ),
        "baseline_evaluation_manifest_sha256": sha256(before_path / "evaluation_manifest.json"),
        "candidate_evaluation_manifest_sha256": sha256(current_eval / "evaluation_manifest.json"),
    }
    return summary, reports


# Read the command-line options, then summarize the evidence for proposed course endings on
# the permitted development samples.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    enforce_policy(BASELINE / "expanded-evaluation", args.output)
    root = args.output.resolve()
    # Guard before opening output or any child path.
    enforce_policy(BASELINE / "expanded-evaluation", root)
    candidate = root / "candidate-v1"
    freeze = read_json(candidate / "source_only_freeze.json")
    # Require the saved source-only prediction snapshot before reporting this comparison.
    if freeze["status"] != "BOTH_PREDICTIONS_SEALED_BEFORE_SCORING":
        raise ValueError("source-only prediction seal missing")
    for path, digest in freeze["parser_sha256"].items():
        # Stop if the parser changed after prediction seal.
        if sha256(WORKSPACE / path) != digest:
            raise ValueError("parser changed after prediction seal")
    results, reports = {}, {}
    for name in ("original", "expanded"):
        results[name], reports[name] = summarize(name, candidate, root)
    write_json(root / "outcomes.json", results)
    sections = []
    for name, result in results.items():
        rows = "".join(
            f"<tr><td>{html.escape(key.replace('_', ' '))}</td><td>{result['before'][key]}</td><td>{result['after'][key]}</td></tr>"
            for key in METRICS
        )
        sections.append(
            f"<section><h2>{name.title()} exposed DEVELOPMENT</h2><table><tr><th>Metric</th><th>Before</th><th>Phase 7</th></tr>{rows}</table><p>{result['boundary_reports_including_context_pages']} boundary reports, {result['scored_proposals']} scored proposals including context pages. {html.escape(str(result['review_alternatives']))}</p><p>Course source records unchanged: <strong>{result['course_records_identical_excluding_version_fields']}</strong>.</p></section>"
        )
    details = []
    for r in reports["expanded"]:
        title = f"{r['code']} {r['title'] or ''} — physical page {r['physical_page']}"
        source = f"../historical-detection-phase5-v1/candidate-v1/expanded-evaluation/pages/{r['dataset_page_id']}/index.html"
        proposal_rows = []
        for p in r["proposals"]:
            rank = p.get("ranking", {})
            cues = "; ".join(f"{c['code']} ({c['weight']:+})" for c in rank.get("cues", []))
            target = p.get("target_node_id") or ", ".join(p.get("boundary_node_ids", []))
            proposal_rows.append(
                f"<tr><td>{html.escape(p['origin'])}<br>{html.escape(p['action'])} / {html.escape(p.get('scope', 'FULL_COURSE'))}</td><td>{html.escape(p['status'])}</td><td>{rank.get('score', '')}</td><td>{html.escape(cues)}<br><small>Unavailable: {html.escape(', '.join(rank.get('unavailable', [])))}</small></td><td>{html.escape(target or '(no target)')}<br>{html.escape(', '.join(p.get('violations', [])))}</td></tr>"
            )
        rows = "".join(proposal_rows)
        details.append(
            f"<details data-search='{html.escape(title.casefold(), quote=True)}'><summary>{html.escape(title)} — {html.escape(r['selection_reason'])}</summary><p><a href='{source}'>Open permitted source window</a> · <strong>{html.escape(r['ranking']['decision'])}</strong>; distinct admissible endpoints: {len(r['ranking']['admissible_endpoint_groups'])}; score margin: {r['ranking']['top_score_margin'] if r['ranking']['top_score_margin'] is not None else 'not applicable'}.</p><div class='scroll'><table><tr><th>Proposal</th><th>Admission</th><th>Score</th><th>Source cues</th><th>Target / violations</th></tr>{rows}</table></div></details>"
        )
    document = (
        """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Phase 7: source boundary competition</title><style>body{font:17px/1.55 system-ui;margin:0;color:#183342;background:#f4f7f8}main{max-width:1200px;margin:auto;padding:36px}h1{font-size:34px;line-height:1.2}h2{font-size:23px}section,details,.notice{background:white;padding:20px;margin:18px 0;border:1px solid #d5e2e8;border-radius:10px}.notice{border-left:6px solid #377381}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;text-align:left;border-bottom:1px solid #dce5e8;vertical-align:top}th{background:#e6eef1}summary{cursor:pointer;font-weight:650}small{color:#4c626c}.scroll{overflow:auto}input{box-sizing:border-box;padding:12px;width:100%;font:inherit;border:1px solid #7694a2;border-radius:6px}a{color:#07527a}.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}@media(max-width:800px){main{padding:18px}.pair{display:block}}[hidden]{display:none}</style><main><h1>Phase 7 — Explain and compare course boundaries</h1><div class='notice'><strong>Result: better diagnostics; no measured extraction gain.</strong><p>Original complete core courses stay 35/52; expanded stay 66/128. The expanded cohort includes the original and is not an independent validation set. The reserved 34-window cohort remains sealed until all 18 phases are complete and the parser is frozen.</p></div><h2>Which suggestions we implemented</h2><p>Boundary proposals now carry explicit scores, source evidence, geometry relative to the page's typical line height, font ratios and missing-evidence states. Correlated cues are capped by family. Equivalent source extents and targets count once; repeated producer outputs are not extra votes. The score is a provisional ranking value, not confidence.</p><p>Prerequisite stops are DESCRIPTION field transitions. They cannot end the full course record or drop prerequisites. Gap and column-change stops remain review alternatives; their layout cues cannot establish a complete record. Cross-page lowercase language supports a continuation hypothesis, but cannot bypass verified order or ownership constraints.</p><h2>What we deliberately deferred</h2><p>Automatic selection between distinct verified extents using a numerical margin. The baseline has zero conflicting verified endpoints across the two permitted runs. No reviewed margin-calibration examples support such a threshold. All distinct verified endpoints remain unresolved; rejected and review-only hypotheses cannot win regardless of score.</p><p>This follows the separation of document structure and nested fields, and relative layout features described in <a href='https://grobid.readthedocs.io/en/latest/Principles/'>GROBID's architecture</a>. That source supports the design choice; it does not prove accuracy gains on university catalogs.</p><div class='pair'>"""
        + "".join(sections)
        + """</div><h2>Expanded source-bound proposal explorer</h2><p>These are parser proposals, including context and pending windows, not new review answers. The detailed record preserves all candidates, cues, score families, measurements and source anchors. Filter by printed code/title:</p><input id='filter' aria-label='Filter boundary proposals' placeholder='Search printed course code or title'><p id='count'></p>"""
        + "".join(details)
        + """<h2>Reproducibility</h2><p><a href='outcomes.json'>Paired metrics</a> · <a href='expanded_boundary_diagnostics.jsonl'>All expanded boundary evidence</a> · <a href='original_boundary_diagnostics.jsonl'>All original boundary evidence</a> · <a href='candidate-v1/source_only_freeze.json'>Source-only parser freeze</a> · <a href='boundary_changes.diff'>Code changes</a> · <a href='verification.json'>Verification</a></p></main><script>const rows=[...document.querySelectorAll('details')];const f=document.querySelector('#filter');function filter(){let n=0;for(const r of rows){r.hidden=!r.dataset.search.includes(f.value.toLowerCase());if(!r.hidden)n++}document.querySelector('#count').textContent=n+' proposal groups shown'}f.addEventListener('input',filter);filter();</script></html>"""
    )
    (root / "index.html").write_text(document)
    changes = []
    for path in (
        "src/course_parser/boundary_proposals.py",
        "src/course_parser/boundary_scoring.py",
        "src/course_parser/contextual.py",
        "src/course_parser/learning.py",
        "src/course_parser/__init__.py",
        "pyproject.toml",
        "tests/test_boundary_scoring.py",
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
    (root / "boundary_changes.diff").write_text("".join(changes))
    write_json(
        root / "phase7_measurement_manifest.json",
        {
            "status": "DIAGNOSTIC_IMPROVEMENT_NO_EXTRACTION_GAIN",
            "reserved_cohort_access": "SEALED",
            "completed_phase": 7,
            "all_18_phases_complete": False,
            "source_only_freeze": freeze,
            "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
            "files_sha256": artifact_hashes(root),
            "outcomes": results,
            "weights_calibrated": False,
            "automatic_conflict_margin_selection_enabled": False,
        },
    )
    print(
        {
            k: {
                "complete_core": v["after"]["complete_content_correct"],
                "unchanged_records": v["course_records_identical_excluding_version_fields"],
                "alternatives": v["review_alternatives"],
            }
            for k, v in results.items()
        }
    )


if __name__ == "__main__":
    main()
