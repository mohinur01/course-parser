# Purpose: Verify the exposed OCR evidence and report routing separately from course accuracy.
# ruff: noqa: E501 -- Standalone report HTML and policy prose.
"""Verify the exposed OCR evidence and report routing separately from course accuracy."""

import argparse
import difflib
import html
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
BASELINE = WORKSPACE / "artifacts/reading-order-phase9-v1/candidate-v2"
METRICS = (
    "gold_courses",
    "matched_course_proposals",
    "complete_content_correct",
    "complete_all_fields_correct",
    "false_course_proposals",
    "ambiguous_course_proposals",
    "description_contamination",
)


# Read the command-line options, then check the saved OCR experiment and build its report.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    enforce_policy(BASELINE / "original-evaluation", args.output)
    root = args.output.resolve()
    candidate = root / "candidate-v2"
    freeze = read_json(candidate / "source_only_freeze.json")
    assert freeze["status"] == "BOTH_PREDICTIONS_AND_OCR_EVIDENCE_SEALED_BEFORE_SCORING"
    for path, digest in freeze["parser_sha256"].items():
        assert sha256(WORKSPACE / path) == digest, path
    assert sha256(WORKSPACE / "scripts/audit_phase10_ocr.py") == freeze["audit_producer_sha256"]
    cached = read_json(candidate / "cached-ocr-audit/audit_manifest.json")
    verify_files(candidate / "cached-ocr-audit", cached["files_sha256"])
    assert (
        sha256(candidate / "cached-ocr-audit/audit_manifest.json")
        == freeze["cached_audit_manifest_sha256"]
    )
    for record in read_jsonl(candidate / "cached-ocr-audit/preserved_artifacts.jsonl"):
        assert sha256(Path(record["path"])) == record["sha256"]
    inbox = candidate / "gated-omission-review"
    review = read_json(inbox / "review_manifest.json")
    verify_files(inbox, review["file_sha256"])
    assert sha256(inbox / "review_manifest.json") == freeze["gated_review_manifest_sha256"]
    assert all(
        t["disposition"] is None and t["course_truth_assigned"] is False
        for t in read_jsonl(inbox / "review_queue.jsonl")
    )
    prep = read_json(inbox / "preparation_manifest.json")
    results, tables = {}, []
    for name in ("original", "expanded"):
        baseline = BASELINE / (name + "-evaluation")
        enforce_policy(baseline, root)
        before = read_json(baseline / "evaluation_manifest.json")
        after = read_json(candidate / (name + "-evaluation/evaluation_manifest.json"))
        assert before["pilot_manifest_sha256"] == after["pilot_manifest_sha256"]
        for base, manifest in ((BASELINE, before), (candidate, after)):
            evaluation = base / (name + "-evaluation")
            verify_files(evaluation, manifest["file_sha256"])
            assert manifest["evaluation_code_sha256"] == sha256(
                WORKSPACE / "src/course_parser/learning_evaluation.py"
            )
            predictions = base / (name + "-predictions")
            pm = read_json(predictions / "prediction_manifest.json")
            verify_files(predictions, pm["file_sha256"])
            assert manifest["prediction_manifest_sha256"] == sha256(
                predictions / "prediction_manifest.json"
            )
            assert pm["ordering_engine"] == "source_rows"

        # Remove record-version bookkeeping when comparing the actual saved content.
        def content(base, name=name):
            return [
                {k: v for k, v in r.items() if not k.endswith("_version")}
                for r in read_jsonl(base / (name + "-predictions/course_proposals.jsonl"))
            ]

        result = {
            "before": {k: before["overall"][k] for k in METRICS},
            "after": {k: after["overall"][k] for k in METRICS},
            "native_course_records_identical": content(BASELINE) == content(candidate),
            "core_eligible": after["overall"]["complete_content_eligible"],
            "all_fields_eligible": after["overall"]["complete_all_fields_eligible"],
        }
        results[name] = result
        rows = "".join(
            f"<tr><td>{k.replace('_', ' ')}</td><td>{result['before'][k]}</td><td>{result['after'][k]}</td></tr>"
            for k in METRICS
        )
        tables.append(
            f"<section><h2>{name.title()} exposed cohort</h2><table><tr><th>Metric</th><th>Phase 9</th><th>Phase 10</th></tr>{rows}</table><p>Native source course records identical: {result['native_course_records_identical']}. Expanded includes original; these are not independent samples.</p></section>"
        )
    reconciliations = read_jsonl(candidate / "cached-ocr-audit/reconciliations.jsonl")
    details = []
    for item in reconciliations:
        crop = Path(item["crop_path"])
        extracts = []
        for path in sorted(crop.glob("*/extraction.json")):
            record = read_json(path)
            extracts.append(
                f"<article><h3>{html.escape(path.parent.name)}</h3><p>{html.escape(record['status'])}</p><pre>{html.escape(record['raw_text'] or '')}</pre><p>{html.escape(str(record['review_flags']))}</p><a href='../omission-extractions-v1/release-v4/crops/{crop.name}/{path.parent.name}/extraction.json'>Separate raw record</a></article>"
            )
        details.append(
            f"<details><summary>{html.escape(item['flag_id'])} · {item['route']['decision']} → {item['decision']}</summary><p>Physical page {item['route']['physical_page']} · TUNING · pending source review. Native text is present: {item['route']['quality']['native_text_present']}.</p><img src='../omission-extractions-v1/release-v4/crops/{crop.name}/crop.png' alt='Preserved diagnostic crop'><div class='grid'>{''.join(extracts)}</div><p>Raw outputs and transforms unchanged. <a href='../omission-extractions-v1/release-v4/crops/{crop.name}/transform.json'>Crop transform</a>. Crops include context; they do not assert a course boundary.</p></details>"
        )
    document = f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Phase 10 targeted OCR</title><style>body{{font:17px/1.55 system-ui;background:#f3f6f8;color:#183442;margin:0}}main{{max-width:1250px;padding:35px;margin:auto}}section,details,.notice,article{{padding:20px;border:1px solid #d2dfe4;border-radius:10px;background:white;margin:18px 0}}.notice{{border-left:6px solid #367b83}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #d7e2e6}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}}summary{{cursor:pointer;font-weight:650}}img{{max-width:100%;height:auto}}a{{color:#07577d}}@media(max-width:800px){{main{{padding:18px}}.grid{{display:block}}}}</style><main><h1>Phase 10 — Recover evidence with targeted OCR</h1><div class='notice'><strong>Source-quality gating and reconciliation implemented. Course extraction accuracy unchanged.</strong><p>Complete core courses remain 35/52 original and 66/128 expanded; zero known false-course proposals in both. The reserved 34-window institution-held-out cohort remains sealed until all 18 phases are complete and the parser is frozen.</p></div><h2>Assessment of the suggestions</h2><p>The source-preserving design is appropriate and mostly existed already. We added the missing quality gate and explicit recovery decisions. A missing course proposal can be a structure or ownership error even when the native text is readable. Disagreement is a source-review question; multiple engines can share an error. Two Tesseract segmentation modes count as one engine family. The example GE0L → GEOL is therefore a proposed source correction, never an automatic character repair.</p><h2>Routing now used by the software</h2><p>Failed, absent or explicitly damaged native text can request a bounded OCR probe. Crop-edge intersections require bounds review first. Cross-column native lines require column-local crops, and table context needs table/row structure review before OCR. Readable native text keeps its original representation; omission flags alone route structural review. Empty native text may indicate a blank area, so neither an empty OCR result nor an unavailable engine creates a negative label.</p><p>Each extraction retains raw text, TXT/TSV/JSON, settings, scores, hashes and its crop → page → PDF transforms. Reconciliation preserves alternative readings and asks for source review. It creates no consensus text, accepted course, keyword disposition or training label. Optional PP obeys the same gate and skips initialization when no crop is eligible. PSM 11 provides sparse recognition without certifying reading order, as described in <a href='https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html'>Tesseract's segmentation guidance</a>; separate TXT/TSV evidence follows its <a href='https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html'>command-line documentation</a>.</p><h2>Source-only routing evidence</h2><p>The existing 12 TUNING crops retain all {cached["retained_extraction_records"]} separate extraction records. Retrospective route counts: {html.escape(str(cached["routing_counts"]))}. Eleven have crop-edge intersections; the remaining crop has readable native text. No new OCR is justified on these unchanged crops. The gate requests {cached["source_gate_tesseract_calls_on_same_crops"]} Tesseract calls instead of {cached["hypothetical_tesseract_calls_if_every_crop_were_ocr"]} all-crop calls. This measures routing, not recovered-course accuracy.</p><p>A fresh producer run on current exposed predictions materialized {len(prep["crop_paths"])} crops, requested {prep["ocr_executions_requested"]} Tesseract executions, and retained {review["review_tasks"]} blank source-review tasks. Its route counts are {html.escape(str(prep["ocr_routing_counts"]))}. All original omission observations remain available; skipping OCR does not dismiss the question. Optional PP result: {html.escape(str(freeze["optional_pp"]))}.</p><p>A real Tesseract regression recovers Mineralogy text from a deterministic image-only PDF, preserves the empty native record, and returns REVIEW_RECOVERED_TEXT. A blank-page regression stays SOURCE_INPUT_INSUFFICIENT. These tests establish behavior, not accuracy on previously unseen university catalogs.</p><div class='grid'>{"".join(tables)}</div><h2>Retained tuning readings</h2><p>Inspect the original source page before accepting a reading. Crop bounds can explain apparent OCR differences. Every alternative remains pending review.</p>{"".join(details)}<h2>Evidence and verification</h2><p><a href='candidate-v2/gated-omission-review/index.html'>Fresh gated omission inbox</a> · <a href='candidate-v2/cached-ocr-audit/reconciliations.jsonl'>Every preserved competing reading</a> · <a href='candidate-v2/cached-ocr-audit/preserved_artifacts.jsonl'>Original artifact hashes</a> · <a href='candidate-v2/source_only_freeze.json'>Source-only freeze</a> · <a href='outcomes.json'>Paired strict metrics</a> · <a href='verification.json'>Verification</a> · <a href='ocr_recovery_changes.diff'>Changes</a></p></main></html>"""
    document = document.replace(
        "<h2>Retained tuning readings</h2>",
        "<h2>A source spot-check</h2><p>The printed Conservation of Natural Resources title ends in III. Native text preserves III, while both Tesseract modes return II!. However, the printed prerequisite abbreviation is equiv.; native returns equ/V. and both OCR modes preserve equiv. This crop shows why neither engine should globally replace the other, and why agreement between two modes of one engine is not independent evidence. Each conflicting reading remains available for source review. <a href='source_spotcheck.json'>Image and extraction hashes</a>.</p><h2>Retained tuning readings</h2>",
    )
    (root / "index.html").write_text(document)
    write_json(root / "outcomes.json", results)
    changes = []
    for path in (
        "src/course_parser/ocr_recovery.py",
        "src/course_parser/omission_review.py",
        "src/course_parser/crop_ppstructure.py",
        "src/course_parser/__init__.py",
        "tests/test_ocr_recovery.py",
        "tests/test_omission_review.py",
        "scripts/audit_phase10_ocr.py",
        "scripts/measure_phase10_ocr.py",
        "scripts/build_phase10_ocr_report.py",
        "config/development_phase_execution_v1.json",
        "pyproject.toml",
        "docs/ocr_recovery_phase10.md",
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
    (root / "ocr_recovery_changes.diff").write_text("".join(changes))
    write_json(
        root / "phase10_measurement_manifest.json",
        {
            "status": "TARGETED_OCR_GATING_NO_COURSE_EXTRACTION_GAIN",
            "phase": 10,
            "all_18_phases_complete": False,
            "reserved_cohort_access": "SEALED",
            "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
            "source_only_freeze": freeze,
            "outcomes": results,
            "cached_audit": cached,
            "automatic_text_replacement": False,
            "new_course_truth_labels": 0,
            "files_sha256": artifact_hashes(root),
        },
    )


if __name__ == "__main__":
    main()
