# Check the blank tuning annotation packets and choose a small initial review batch. Prepare
# the evidence for sequence labeling without training a model.
"""Audit blank TUNING sequences and prepare a small source-review queue; never train."""

from __future__ import annotations

import argparse
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
from course_parser.omission_review import _reject_reserved_paths
from course_parser.reference import ReferenceProject
from course_parser.sequence_annotations import (
    audit_sequence_reviews,
    export_sequence_templates,
    source_order_digest,
)

WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE = WORKSPACE / "artifacts/ocr-recovery-phase10-v1/candidate-v2"
HISTORICAL = WORKSPACE / "artifacts/sequence-boundaries-v3/release/sequence_annotations"
PILOT = WORKSPACE / "artifacts/source-learning-v4/pilot"
AUDIT = WORKSPACE / "artifacts/validation-transfer-v1/audit"


# Prioritize source review, retaining a negative and a strict batch-size bound.
def select_initial_windows(rows, pages, per_page, limit=6):
    """Prioritize source review, retaining a negative and a strict batch-size bound."""
    ranked = sorted(
        [r for r in rows if r["units"]],
        key=lambda r: (-sum(per_page.get(r["dataset_page_id"], {}).values()), r["dataset_page_id"]),
    )
    negatives = [r for r in ranked if pages.get(r["dataset_page_id"], {}).get("gold_courses") == 0]
    selected = negatives[:1]
    seen_groups = {r["learning_group"] for r in selected}
    for row in ranked:
        if len(selected) >= limit:
            break
        if row["learning_group"] not in seen_groups:
            selected.append(row)
            seen_groups.add(row["learning_group"])
    for row in ranked:
        if len(selected) >= limit:
            break
        if row not in selected:
            selected.append(row)
    return selected, negatives


# Read the command-line options, then check the blank tuning annotation packets and choose a
# small initial review batch.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _reject_reserved_paths(args.output, args.reference_project)
    for name in ("original", "expanded"):
        enforce_policy(BASELINE / (name + "-evaluation"), args.output)
    args.output.mkdir(parents=True, exist_ok=False)
    ReferenceProject(args.reference_project)
    historical = audit_sequence_reviews(HISTORICAL, HISTORICAL / "sequence_template.jsonl")
    write_json(args.output / "historical_readiness.json", historical)
    templates = args.output / "tuning-templates"
    export_sequence_templates(PILOT, BASELINE / "original-predictions", templates)
    readiness = audit_sequence_reviews(templates, templates / "sequence_template.jsonl")
    write_json(args.output / "current_readiness.json", readiness)
    rows = read_jsonl(templates / "sequence_template.jsonl")
    evaluated, evaluation_hashes = {}, {}
    for name in ("original", "expanded"):
        path = BASELINE / (name + "-evaluation")
        value = read_json(path / "evaluation_manifest.json")
        verify_files(path, value["file_sha256"])
        evaluated[name] = value
        evaluation_hashes[name] = sha256(path / "evaluation_manifest.json")
    original = BASELINE / "original-evaluation"
    errors = read_jsonl(original / "errors.jsonl")
    pages = {p["dataset_page_id"]: p for p in read_jsonl(original / "page_metrics.jsonl")}
    per_page = {}
    for error in errors:
        if error["learning_partition"] == "TUNING":
            per_page.setdefault(error["dataset_page_id"], Counter()).update(
                [error.get("field") or error["error_type"]]
            )
    selected, negatives = select_initial_windows(rows, pages, per_page)
    write_jsonl(args.output / "first_batch_template.jsonl", selected)
    plan = []
    for rank, row in enumerate(selected, 1):
        plan.append(
            {
                "review_rank": rank,
                "dataset_page_id": row["dataset_page_id"],
                "learning_partition": "TUNING",
                "learning_group": row["learning_group"],
                "source_packet_path": row["source_packet_path"],
                "source_units": len(row["units"]),
                "source_order_sha256": source_order_digest(row),
                "exposed_field_mismatch_counts": dict(per_page.get(row["dataset_page_id"], {})),
                "reviewed_zero_course_window": row in negatives,
                "review_scope": "Whole central/context window, including every noncourse character",
                "labels_supplied": False,
                "instruction": "Two humans check images, order, owners, fields and boundaries; "
                "preserve exact characters and split mixed-field nodes. "
                "Leave uncertainty PENDING. Never copy parser labels as gold.",
            }
        )
    write_json(
        args.output / "first_annotation_batch.json",
        {
            "status": "BLANK_TUNING_SOURCE_REVIEW_PLAN",
            "windows": plan,
            "note": "Small first batch, not a sufficient training sample or model target. "
            "Course-level review answers prioritize review only; no sequence labels inferred.",
        },
    )
    old_rows = read_jsonl(AUDIT / "validation_failure_matrix_15.jsonl")
    old_counts = Counter(c for r in old_rows for c in r["failure_classes"])
    mismatch_counts = Counter(e["field"] for e in errors if e["error_type"] == "FIELD_MISMATCH")
    outcomes = {
        "status": "CRF_DEFERRED_ANNOTATION_SAFEGUARDS_IMPLEMENTED",
        "crf_installed_or_trained": False,
        "protected_evaluation_opened": False,
        "historical_template_units": read_json(HISTORICAL / "manifest.json")["source_units"],
        "current_template_units": read_json(templates / "manifest.json")["source_units"],
        "annotation_readiness": readiness,
        "exposed_15_diagnostic_historical_failure_classes": dict(old_counts),
        "historical_failure_matrix_sha256": sha256(AUDIT / "validation_failure_matrix_15.jsonl"),
        "current_original_field_mismatches": dict(mismatch_counts),
        "transition_need_certified": False,
        "interpretation": "One historical FIELD_SEQUENCE_AMBIGUITY case, three inline metadata "
        "cases and four prerequisite-label convention cases justify targeted "
        "source review. Their counts overlap and are pre-fix diagnostics; "
        "field mismatches are not evidence of transition root causes. "
        "Identifier, source-absence and OCR problems require separate treatment.",
        "accuracy_measurement": "No new parser run or rescore; accepted Phase 10 results reused. "
        "This phase changes annotation auditing, not extraction.",
        "accepted_baseline": {name: value["overall"] for name, value in evaluated.items()},
        "baseline_evaluation_manifest_sha256": evaluation_hashes,
        "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
        "sources": [
            "https://www.chokkan.org/software/crfsuite/manual.html",
            "https://sklearn-crfsuite.readthedocs.io/en/latest/tutorial.html",
        ],
    }
    write_json(args.output / "outcomes.json", outcomes)
    entries = "".join(
        f"<tr><td>{p['review_rank']}</td><td>{html.escape(p['dataset_page_id'])}</td>"
        f"<td>{p['source_units']}</td><td>{html.escape(str(p['exposed_field_mismatch_counts']))}</td>"
        f"<td>{'Yes' if p['reviewed_zero_course_window'] else 'No'}</td></tr>"
        for p in plan
    )
    report = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Phase 11: CRF readiness</title><style>
body{{font:17px/1.55 system-ui;max-width:1000px;margin:40px auto;padding:0 24px;color:#233044}}
h1{{line-height:1.15}} table{{border-collapse:collapse;width:100%}}
td,th{{padding:10px;text-align:left;
border-bottom:1px solid #ccd5df}}a{{color:#18559d}}.note{{background:#eef3fa;padding:16px}}
</style><h1>Phase 11: defer CRF training</h1>
<p class="note">18 TUNING windows; {outcomes["current_template_units"]:,} current source units;
zero adjudicated sequence labels. Reserved 34-window cohort remains sealed.</p>
<h2>Assessment</h2><p>The suggestion is appropriate. A sequence model is plausible for field
transitions, but neither a blank label packet nor a field mismatch establishes that it will improve
complete course extraction. Historical examples motivate source annotation; they do not prove a
current dominant transition failure. No CRF was installed, trained or enabled.</p>
<p>The historical packet really contains 6,412 units. Fresh templates use the accepted native
Phase 10 proposals. Both old and new packets remain blank.</p>
<h2>Implemented safeguards</h2><ul><li>Block reserved paths and aliases before sequence input reads
or output writes.</li><li>Require two declared distinct humans and an order check bound to exact
frozen source units; parser COMPLETE status does not confirm human reading order.</li>
<li>Reject uncertain adjudicated boundaries, dangling endings, reopened course owners, duplicate
identities and source mutation.</li><li>Keep source-insufficient windows out of training counts.
Pending/model-filled labels receive no training credit.</li></ul>
<h2>Annotation format</h2><p>Each source span has a field role, an anchored course owner and a
boundary relation. Course starts/ends belong to that ownership layer. Do not insert B-COURSE or
E-COURSE as artificial source tokens. A future field BIO encoding must be derived from reviewed
spans within owned courses, without losing exact character offsets.</p>
<p>The <a href="https://www.chokkan.org/software/crfsuite/manual.html">CRFsuite manual</a>
describes one label per sequence item and learned state/transition features. Our separation of
course boundaries and fields is a project design choice. The
<a href="https://sklearn-crfsuite.readthedocs.io/en/latest/tutorial.html">official tutorial</a>
illustrates labeled sequences and features; its token scores would be auxiliary here.</p>
<h2>Small first annotation batch</h2><p>Six whole TUNING windows, selected across available
institution families, field mismatches and a reviewed zero-course window. These are review
questions, not training labels. Review all source characters, not only keyword hits.</p>
<table><thead><tr><th>Rank</th><th>Window</th><th>Units</th><th>Review priority signals</th>
<th>Known negative page</th></tr></thead><tbody>{entries}</tbody></table>
<h2>Requirements for a future experiment</h2><p>Reliable positive courses and hard negatives;
source-reviewed evidence that transition errors still matter; a separate institution-grouped
sequence validation split from permitted material with overlapping pages/catalog families kept
together. Do not use the sealed 34-window cohort. Exclude institution names, paths, keywords and
review outcomes as model features. A future experiment must improve complete-course extraction
without worsening false ownership, contamination or abstentions.
Token accuracy alone is insufficient.
No numerical annotation count alone certifies readiness.</p>
<h2>Accuracy</h2><p>Extraction code and strict scorer are unchanged. Accepted Phase 10 results
are reused, not newly scored: original 35/52 complete core courses, 10/38 all fields; expanded
66/128 core, 13/97 all fields. Zero measured false courses in both. Expanded includes original.
This phase improves annotation integrity; it claims no course-accuracy gain.</p>
<p><a href="current_readiness.json">Readiness audit</a> ·
<a href="first_annotation_batch.json">First batch and order digests</a> ·
<a href="first_batch_template.jsonl">Six blank annotation windows</a> ·
<a href="tuning-templates/sequence_template.jsonl">Blank source templates</a> ·
<a href="tuning-templates/label_contract.json">Label contract</a> ·
<a href="outcomes.json">Evidence and baseline hashes</a></p></html>"""
    (args.output / "index.html").write_text(report)
    write_json(
        args.output / "manifest.json",
        {
            "status": outcomes["status"],
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(args.output),
        },
    )
    print(
        {k: outcomes[k] for k in ("status", "historical_template_units", "current_template_units")}
    )


if __name__ == "__main__":
    main()
