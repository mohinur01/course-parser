# Purpose: Diagnose sealed audit flags with existing exposed answers; never change scores.
# ruff: noqa: E501 -- HTML report fragments and metric-definition prose
"""Diagnose sealed audit flags with existing exposed answers; never change scores."""

from __future__ import annotations

import argparse
import html
from collections import Counter, defaultdict
from pathlib import Path

from course_parser.completion_diagnostics import load_exposed_evaluation
from course_parser.field_scoring import enforce_policy
from course_parser.io import artifact_hashes, read_json, read_jsonl, sha256, write_json, write_jsonl
from course_parser.omission_ranking import overlap_fraction

WORKSPACE = Path(__file__).resolve().parents[1]
PHASE5 = WORKSPACE / "artifacts/historical-detection-phase5-v1/candidate-v1"


# Return the text location as a bounding box, combining boxes when needed.
def box(evidence):
    geometry = evidence.get("geometry")
    if not geometry or geometry.get("coordinate_system") != "PDF_POINTS_CANONICAL":
        return None
    return [geometry[key] for key in ("x0", "y0", "x1", "y1")]


# Mutually unique source-header geometry links, not new course truth.
def course_flag_links(flags, courses):
    """Mutually unique source-header geometry links, not new course truth."""
    edges, reverse = defaultdict(set), defaultdict(set)
    for flag in flags:
        for course in courses:
            if (
                flag["dataset_page_id"] != course["dataset_page_id"]
                or flag["physical_page"] != course["header_physical_page"]
            ):
                continue
            anchors = [
                box(e)
                for field in ("code", "title")
                for e in course[field]["evidence"]
                if e["physical_page"] == course["header_physical_page"]
            ]
            if any(
                anchor and overlap_fraction(flag["geometry"], anchor) >= 0.5 for anchor in anchors
            ):
                edges[flag["flag_id"]].add(course["gold_course_id"])
                reverse[course["gold_course_id"]].add(flag["flag_id"])
    linked = {fid: next(iter(gids)) for fid, gids in edges.items() if len(gids) == 1}
    return linked, {fid: sorted(gids) for fid, gids in edges.items() if len(gids) > 1}, reverse


# Describe the source-reviewed reason for each exposed diagnostic flag.
def diagnose(name, run):
    evaluation = PHASE5 / (name + "-evaluation")
    enforce_policy(evaluation, run)
    diagnostics, provenance = load_exposed_evaluation(evaluation)
    metrics = read_jsonl(evaluation / "page_metrics.jsonl")
    scored = {p["dataset_page_id"] for p in metrics if p["status"] == "SCORED_DEVELOPMENT"}
    negative = {
        p["dataset_page_id"]
        for p in read_jsonl(evaluation / "imported_page_reviews.jsonl")
        if p["dataset_page_id"] in scored
        and p["complete_course_inventory"] is True
        and p["source_visible_course_count"] == 0
    }
    courses = [
        c
        for c in read_jsonl(evaluation / "imported_course_truth.jsonl")
        if c["dataset_page_id"] in scored and c["scope"] == "HEADER_STARTS_ON_CENTRAL_PAGE"
    ]
    by_id = {d["gold_course_id"]: d for d in diagnostics}
    baseline = read_jsonl(run / (name + "_baseline_flags.jsonl"))
    current = read_jsonl(run / (name + "_flags.jsonl"))
    before_links, before_ambiguous, _ = course_flag_links(baseline, courses)
    links, ambiguous, _ = course_flag_links(current, courses)
    # Alignment ambiguity is not an established missing course. Account for it separately,
    # along with covered courses, rather than inflating recall.
    omission_ids = {
        d["gold_course_id"]
        for d in diagnostics
        if d["pipeline_state"] in {"ENTRY_WITHHELD", "NO_ALIGNED_HEADER"}
    }
    source_targets = {c["gold_course_id"] for c in courses if c["gold_course_id"] in omission_ids}
    before_covered = set(before_links.values()) & source_targets
    covered = set(links.values()) & source_targets
    lost = before_covered - covered
    before_key = {(f["dataset_page_id"], f["line_id"]): f for f in baseline}
    current_key = {(f["dataset_page_id"], f["line_id"]): f for f in current}
    flag_rows = [
        {
            "flag_id": f["flag_id"],
            "dataset_page_id": f["dataset_page_id"],
            "raw_text": f["raw_text"],
            "priority_tier": f["priority_tier"],
            "omission_score": f["omission_score"],
            "source_page_has_reviewed_zero_course_inventory": f["dataset_page_id"] in negative,
            "unique_existing_gold_header_geometry_link": links.get(f["flag_id"]),
            "ambiguous_existing_gold_header_links": ambiguous.get(f["flag_id"], []),
            "linked_course_pipeline_state": by_id[links[f["flag_id"]]]["pipeline_state"]
            if f["flag_id"] in links
            else None,
            "course_truth_assigned": False,
        }
        for f in current
    ]
    course_rows = [
        {
            "gold_course_id": c["gold_course_id"],
            "dataset_page_id": c["dataset_page_id"],
            "printed_code": c["code"]["raw"],
            "printed_title": c["title"]["raw"],
            "pipeline_state": by_id[c["gold_course_id"]]["pipeline_state"],
            "baseline_header_flags": [
                fid for fid, gid in before_links.items() if gid == c["gold_course_id"]
            ],
            "current_header_flags": [
                fid for fid, gid in links.items() if gid == c["gold_course_id"]
            ],
            "current_priority_tiers": sorted(
                {
                    f["priority_tier"]
                    for f in current
                    if links.get(f["flag_id"]) == c["gold_course_id"]
                }
            ),
        }
        for c in courses
        if c["gold_course_id"] in source_targets
    ]
    write_jsonl(run / (name + "_flag_diagnostics.jsonl"), flag_rows)
    write_jsonl(run / (name + "_omission_target_coverage.jsonl"), course_rows)
    frozen = read_json(evaluation / "evaluation_manifest.json")["overall"]
    return {
        "exposed_evaluation_manifest_sha256": provenance["evaluation_manifest_sha256"],
        "reviewed_zero_course_pages": len(negative),
        "baseline_flags_on_reviewed_zero_course_pages": sum(
            f["dataset_page_id"] in negative for f in baseline
        ),
        "current_flags_on_reviewed_zero_course_pages": sum(
            f["dataset_page_id"] in negative for f in current
        ),
        "current_negative_page_flags_by_tier": dict(
            Counter(f["priority_tier"] for f in current if f["dataset_page_id"] in negative)
        ),
        "omission_targets": len(source_targets),
        "baseline_targets_with_unique_header_flag": len(before_covered),
        "current_targets_with_unique_header_flag": len(covered),
        "newly_flagged_omission_targets": len(covered - before_covered),
        "previously_flagged_omission_targets_lost": len(lost),
        "lost_target_ids": sorted(lost),
        "covered_omission_targets_by_tier": {
            tier: len(
                {
                    links[f["flag_id"]]
                    for f in current
                    if f["flag_id"] in links
                    and links[f["flag_id"]] in source_targets
                    and f["priority_tier"] == tier
                }
            )
            for tier in ("HIGH", "MEDIUM", "LOW")
        },
        "ambiguous_flag_header_links": len(ambiguous),
        "baseline_ambiguous_flag_header_links": len(before_ambiguous),
        "new_flag_observations": len(set(current_key) - set(before_key)),
        "no_longer_generated_flag_observations": len(set(before_key) - set(current_key)),
        "frozen_extraction_metrics": {
            k: frozen[k]
            for k in (
                "complete_content_correct",
                "complete_content_eligible",
                "complete_all_fields_correct",
                "complete_all_fields_eligible",
                "false_course_proposals",
                "ambiguous_course_proposals",
            )
        },
        "flags_individually_adjudicated": False,
        "ranking_precision_estimated": False,
        "coverage_note": "Existing source-header code/title boxes, >=0.5 smaller-box overlap, unique flag-to-course link. Ambiguous alignments excluded from omission targets. This is diagnostic audit coverage, not extraction accuracy or independent omission adjudication.",
    }


# Read the command-line options, then explain which saved omission flags correspond to
# reviewed courses or other source text.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    freeze = read_json(args.run / "source_only_freeze.json")
    # Save the source-only audit before comparing its flags with review answers.
    if (
        freeze["status"] != "BOTH_AUDIT_RUNS_SEALED_BEFORE_ANSWER_DIAGNOSTICS"
        or freeze["reserved_cohort_access"] != "SEALED"
    ):
        raise ValueError("audit source-only freeze required before diagnostics")
    for name, provenance in freeze["provenance"].items():
        for suffix, key in (
            ("_baseline_flags.jsonl", "baseline_flags_sha256"),
            ("_flags.jsonl", "flags_sha256"),
            ("_review_groups.jsonl", "groups_sha256"),
        ):
            # Stop if the sealed flags changed.
            if sha256(args.run / (name + suffix)) != provenance[key]:
                raise ValueError("sealed flags changed")
    for name, digest in freeze["audit_code_sha256"].items():
        # Stop if the audit code changed after freeze.
        if sha256(WORKSPACE / "src/course_parser" / name) != digest:
            raise ValueError("audit code changed after freeze")
    results = {name: diagnose(name, args.run) for name in ("original", "expanded")}
    write_json(args.run / "diagnostic_summary.json", results)
    print(results, flush=True)
    esc = html.escape
    rows = "".join(
        f"<tr><td>{name}</td><td>{freeze['provenance'][name]['baseline_flags']} → {freeze['provenance'][name]['flags']}</td><td>{result['baseline_targets_with_unique_header_flag']} → {result['current_targets_with_unique_header_flag']} / {result['omission_targets']}</td><td>{result['current_negative_page_flags_by_tier']}</td></tr>"
        for name, result in results.items()
    )
    expanded = results["expanded"]
    flags = read_jsonl(args.run / "expanded_flag_diagnostics.jsonl")
    high = "".join(
        f"<tr><td>{esc(f['raw_text'])}</td><td>{f['omission_score']}</td><td>{esc(f['linked_course_pipeline_state'] or 'Pending / no unique header link')}</td></tr>"
        for f in flags
        if f["priority_tier"] == "HIGH"
    )
    doc = f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Phase 6 omission audit</title><style>body{{font:17px system-ui;max-width:1120px;margin:36px auto;padding:0 22px;color:#24394b;background:#f4f7fa}}article{{background:white;padding:22px;margin:22px 0;border:1px solid #c4d4e2;border-radius:9px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:11px;border:1px solid #c4d4e2;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#075d9e}}</style><body>
<h1>Phase 6: rank omission questions</h1><p>DEVELOPMENT / TUNING / old exposed diagnostics only. Reserved 34-window institution-held-out cohort remains sealed until all 18 phases are complete and the parser is frozen.</p>
<article><h2>Decision</h2><p>Adopt separate audit ranking, source-bound evidence, and lossless duplicate grouping. Keep the suggested 8 / 5 cutoffs as provisional review tiers. Points are not probabilities, and review answers never feed scores. None of these flags creates a course, supplies a label, or changes the authoritative hit ledger.</p><p>Same printed title at distinct source positions remains separate. Group only matching source checksum, physical page, coordinate frame, normalized text and at least 85% all-pair overlap of the smaller box. Every original observation remains in the group record. No actual duplicate groups were found in these samples; no deduplication gain is claimed.</p></article>
<article><h2>Measured audit coverage</h2><table><tr><th>Overlapping sample</th><th>All flags, before → after</th><th>Unserved reviewed headers flagged</th><th>After: flags on zero-course pages by tier</th></tr>{rows}</table><p>The original sample is included in the expanded sample. These are exposed diagnostics, not generalization results. Coverage links flags to existing reviewed header boxes; ambiguous alignments are excluded. Low-priority questions stay available. Total review work may grow because decimal headers and other split text now have a recall path.</p><p>{expanded["newly_flagged_omission_targets"]} previously unflagged reviewed omission targets gained a header question; {expanded["previously_flagged_omission_targets_lost"]} previously covered targets lost theirs. Individual flags on course-containing or pending pages have not all been adjudicated, so precision and calibrated confidence are unavailable.</p><p><a href='{esc(args.run.name)}/expanded_omission_target_coverage.jsonl'>Every reviewed omission target</a> · <a href='{esc(args.run.name)}/expanded_flag_diagnostics.jsonl'>Flag diagnostics</a> · <a href='{esc(args.run.name)}/diagnostic_summary.json'>All diagnostic counts</a></p></article>
<article><h2>High-priority questions</h2><table><tr><th>Source text</th><th>Points</th><th>Existing diagnostic link</th></tr>{high}</table><p>Model-supported neighboring courses are corroboration, not confirmed human truth. Requirement, administrative, contents and organization cues reduce priority without suppressing the question.</p></article>
<article><h2>Extraction remains frozen</h2><p>Complete core records remain 35/52 original and 66/128 expanded. All-field records remain 10/38 and 13/97. The parser's extraction modules, strict scorer and course proposals are unchanged by checksum. Audit improvements are not counted as extracted courses.</p><p><a href='{esc(args.run.name)}/source_only_freeze.json'>Both source-only audit runs sealed before diagnostics</a> · <a href='review-inbox-final/index.html'>Blank source-review inbox with separate crop outputs</a></p></article>
<article><h2>Basis and limitations</h2><p>Native geometry revealed separate decimal identifiers and title fragments, credit numbers, running page headers, and repeated genuine titles at different locations. Regression snapshots preserve those observations without inventing gold. Description support stops before the next header; standalone identifiers need adjacent same-row titles.</p><p>The location-and-text principle is consistent with <a href='https://github.com/jsvine/pdfplumber#extracting-text'>pdfplumber duplicate-character handling</a>; our grouping operates on audit observations and retains originals. <a href='https://scikit-learn.org/stable/modules/model_evaluation.html#precision-recall-f-measure-metrics'>Precision/recall definitions</a> require labeled outcomes, so scores are not presented as measured precision.</p><p>The point values and overlap tolerance are heuristics. They need source-reviewed ranking evaluation on TUNING examples; do not calibrate them on the reserved cohort. OCR and table-ownership failures remain extraction work for later phases.</p></article></body></html>"""
    (args.run.parent / "index.html").write_text(doc, encoding="utf-8")
    write_json(
        args.run / "diagnostic_report_manifest.json",
        {
            "phase": 6,
            "scope": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY",
            "reserved_cohort_access": "SEALED",
            "source_only_freeze_sha256": sha256(args.run / "source_only_freeze.json"),
            "file_sha256": artifact_hashes(args.run),
        },
    )


if __name__ == "__main__":
    main()
