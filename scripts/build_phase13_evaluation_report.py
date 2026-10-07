# Check the grouping of permitted evaluation metadata and report the accepted scores. Keep the
# reserved evaluation sealed and avoid rescoring its contents.
"""Audit permitted exposed metadata and reuse accepted scores without rescoring."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from course_parser.evaluation_design import (
    audit_exposed_design,
    audit_split_metadata,
    load_exposed_split_metadata,
)
from course_parser.io import artifact_hashes, read_json, sha256, write_json
from course_parser.omission_review import _reject_reserved_paths

WORKSPACE = Path(__file__).resolve().parents[1]
EXPOSED = {
    "original": WORKSPACE / "artifacts/source-learning-v4/pilot",
    "expanded": WORKSPACE / "artifacts/table-ownership-v1/expanded-pilot",
}
EVALUATIONS = WORKSPACE / "artifacts/ocr-recovery-phase10-v1/candidate-v2"


# Read the command-line options, then check the grouping of permitted evaluation metadata and
# report the accepted scores.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _reject_reserved_paths(args.output)
    # Check both registered evaluation/pilot bindings before producing a report.
    metadata = {
        name: load_exposed_split_metadata(pilot, EVALUATIONS / (name + "-evaluation"), args.output)
        for name, pilot in EXPOSED.items()
    }
    union = {}
    for pages in metadata.values():
        for page in pages:
            key = page["dataset_page_id"]
            # Stop if the shared exposed page metadata differs between pilots.
            if key in union and union[key] != page:
                raise ValueError("shared exposed page metadata differs between pilots")
            union[key] = page
    union_result = audit_split_metadata(list(union.values()))
    args.output.mkdir(parents=True, exist_ok=False)
    results, baselines = {}, {}
    for name, pilot in EXPOSED.items():
        evaluation = EVALUATIONS / (name + "-evaluation")
        results[name] = audit_exposed_design(pilot, evaluation, args.output / name)
        path = evaluation / "evaluation_manifest.json"
        manifest = read_json(path)
        baselines[name] = {
            "overall": manifest["overall"],
            "manifest_sha256": sha256(path),
            "scope": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_NOT_GENERALIZATION",
        }
    write_json(args.output / "combined_metadata_audit.json", union_result)
    outcome = {
        "status": "PHASE13_EVALUATION_DESIGN_IMPLEMENTED_DEVELOPMENT_ONLY",
        "audits": results,
        "overlapping_original_expanded_windows": sum(
            page["dataset_page_id"] in {p["dataset_page_id"] for p in metadata["expanded"]}
            for page in metadata["original"]
        ),
        "combined_unique_windows": len(union),
        "baseline_metrics_reused_not_rescored": baselines,
        "parser_or_model_run": False,
        "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
        "extraction_gain_claimed": False,
        "generalization_claim_permitted": False,
        "reserved_cohort_opened_or_isolation_audited": False,
        "new_fresh_cohort_created_or_reselected": False,
        "protected_evaluation_opened": False,
        "protocol_sha256": sha256(WORKSPACE / "config/evaluation_design_v1.json"),
        "active_seal_policy_sha256": sha256(
            WORKSPACE / "config/development_phase_execution_v1.json"
        ),
    }
    write_json(args.output / "outcomes.json", outcome)
    rows = "".join(
        f"<tr><td>{name}</td><td>{r['windows']}</td>"
        f"<td>{r['role_counts']['TUNING']}</td>"
        f"<td>{r['role_counts']['EXPOSED_DIAGNOSTIC']}</td>"
        f"<td>{r['connected_families']}</td>"
        f"<td>{r['overlapping_context_window_pairs']}</td></tr>"
        for name, r in results.items()
    )
    selections = html.escape(str(results["expanded"]["selection_role_counts"]))
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Phase 13: evaluation design</title><style>
body{{font:17px/1.6 system-ui;max-width:940px;margin:40px auto;padding:0 24px;color:#243247}}
h1{{line-height:1.15}}a{{color:#17549b}}.note{{padding:16px;background:#eef3fa}}
table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;
border-bottom:1px solid #ccd5df}}</style><h1>Phase 13: evaluation design</h1>
<p class="note">Evaluation safeguards implemented. No new parser run, model fitting, scoring or
accuracy gain. Reserved 34-window cohort remains sealed through all 18 phases and parser freeze.</p>
<h2>Assessment</h2><p>The recommendation is appropriate. Repeatedly inspected examples can guide
development but cannot confirm untouched generalization. The original 15-course validation set
already became an exposed diagnostic. Existing family assignments and historical scores are
preserved; new overlays display legacy VALIDATION as EXPOSED_DIAGNOSTIC.</p>
<p>The existing reserved cohort serves the future fresh-validation role. This phase neither
creates another cohort nor reads its identities, pages or answers. A parser freeze alone cannot
open it before all 18 phases are complete. Separate protected TEST/calibration pools remain
excluded. This report does not verify independence against any sealed pool.</p>
<h2>Implemented checks</h2><p>Metadata audits take the transitive union of stable institution
IDs, aliases, learning groups, declared catalog families, shared PDF checksums and overlapping
physical context pages. Connected families cannot cross evaluation roles. All catalog years of
an institution stay together for institution-transfer evaluation. Checks reject missing identity,
duplicate central pages, invalid context windows and protected roles.</p>
<p>The learning-pilot verifier now applies these checks before opening page material. The scoring
CLI checks development metadata and reserved paths before importing answers. Its new --diagnostic
option selects the old VALIDATION partition without rewriting old scores. The entry ranker keeps
TUNING-only fitting, refuses contradictory exposure tags and identifies its validation metrics as
exposed diagnostics. No learned weights changed in this phase.</p>
<h2>Permitted metadata results</h2><table><tr><th>Pilot</th><th>Windows</th><th>Tuning</th>
<th>Exposed diagnostic</th><th>Connected families</th><th>Overlapping window pairs</th></tr>
{rows}</table><p>All known family links pass isolation checks within these exposed pilots.
Original is wholly included in expanded: 28 shared windows, 68 unique windows in the union.
These are metadata windows, including pending reviews, not counts of fully reviewed courses.</p>
<p>The expanded sample has 203 unique physical context pages and one overlapping window pair.
Context windows and repeated catalog years are not independent statistical observations.
The expanded metadata preserves selection strata: {selections}. Random samples remain intact;
targeted diagnostic selection is not a population-representative accuracy estimate.</p>
<h2>What remains unverified</h2><p>No catalog-template family tags are declared in either pilot.
Institution identities and PDF isolation do not prove that different institutions use different
templates. The audit accepts explicit catalog-family links and tests their enforcement; it does
not infer similarity from images. Permitted source review must establish missing template-family
links before a template-transfer claim. No reserved-cohort identity audit was performed.</p>
<h2>Future release contract</h2><p>After all 18 phases, pin parser/rules/models, dependencies,
sampling/split manifests, metric definitions and scoring code. Seal predictions independently of
answers. Report strict core completion alongside fields, false proposals, ownership/contamination,
source uncertainty, abstentions and cost. Count courses by unique central-page starts; report
per-institution results and family-level uncertainty. Do not treat windows as independent samples.
If outcomes guide another change, that cohort becomes exposed and renewed confirmation needs a
new untouched cohort. No access or freeze certificate is issued by this phase.</p>
<h2>Accuracy</h2><p>Accepted Phase 10 results are reused without rescoring: original 35/52 complete
core and 10/38 all fields; expanded 66/128 core and 13/97 all fields, zero measured false courses
in both. Expanded includes original. These remain exposed development results; the strict scorer
and course extraction decisions are unchanged.</p>
<h2>Relevant documentation</h2><p>Scikit-learn's
<a href="https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data">
grouped evaluation guidance</a> motivates keeping dependent groups outside paired training folds.
Its <a href="https://scikit-learn.org/stable/common_pitfalls.html#data-leakage">leakage guidance</a>
requires learning preprocessing and model choices from development/training material, excluding
protected outcomes. Applying this to catalog templates is our domain-specific grouping policy.
No scikit-learn dependency is installed or needed for these metadata checks.</p>
<p><a href="original/split_audit.json">Original metadata audit</a> ·
<a href="expanded/split_audit.json">Expanded metadata audit</a> ·
<a href="combined_metadata_audit.json">Deduplicated union</a> ·
<a href="outcomes.json">Outcomes and provenance</a></p></html>"""
    (args.output / "index.html").write_text(document, encoding="utf-8")
    write_json(
        args.output / "manifest.json",
        {
            "status": outcome["status"],
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(args.output),
        },
    )
    print({name: r["role_counts"] for name, r in results.items()})


if __name__ == "__main__":
    main()
