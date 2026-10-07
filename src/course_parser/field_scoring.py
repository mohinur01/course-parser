# Measure how often the parser delivers each course field correctly. Count missing courses in
# the delivery rates and distinguish unknown source answers from parser mistakes.
"""Phase 2: exact field diagnostics alongside the unchanged strict scorer.

Only approved, sealed, already-exposed DEVELOPMENT evaluations are read. Missing
records earn no end-to-end delivery credit, but are not diagnosed as five field
parser errors. Source-unresolved fields are excluded and counted explicitly.
"""

from __future__ import annotations

import argparse
import html
import os
from collections import Counter
from pathlib import Path

from .completion_diagnostics import load_exposed_evaluation
from .io import artifact_hashes, read_json, read_jsonl, sha256, write_json, write_jsonl
from .learning_evaluation import FIELDS, RESOLVED_FIELDS

WORKSPACE = Path(__file__).resolve().parents[2]
POLICY_PATH = WORKSPACE / "config/development_phase_execution_v1.json"
CONTINUATIONS = {"NONE", "CONFIRMED_CROSS_COLUMN", "CONFIRMED_CROSS_PAGE"}
PANEL_ITEMS = (
    "code",
    "title",
    "description",
    "start_boundary",
    "end_boundary",
    "continuation",
    "credits",
    "prerequisites",
)


# Return a fraction when there is something to count; leave an empty denominator undefined.
def rate(correct: int, eligible: int) -> float | None:
    return correct / eligible if eligible else None


# Check access permissions before opening the evaluation manifest; only registered exposed
# inputs are allowed.
def enforce_policy(evaluation: Path, output: Path) -> dict:
    """Refuse forbidden/unregistered paths before opening an input manifest."""
    policy = read_json(POLICY_PATH)
    # Phase execution policy must retain the 18-phase reserved-cohort seal.
    if (
        policy["schema_version"] != "course-parser-development-phase-execution-v1"
        or policy["reserved_cohort_access"] != "SEALED"
        or policy["total_phases"] != 18
    ):
        raise ValueError("phase execution policy must retain the 18-phase reserved-cohort seal")
    blocked_roots = [WORKSPACE / x for x in policy["blocked_paths"]]
    # Reject named reserved paths lexically, before any source-path filesystem lookup.
    # Check both the requested input and output paths before opening evaluation files.
    for argument in (evaluation, output):
        lexical = Path(os.path.abspath(argument))
        # Keep reserved evaluation data out of this development workflow.
        if any(lexical == root or lexical.is_relative_to(root) for root in blocked_roots):
            raise ValueError("reserved cohort access is forbidden during these phases")
        resolved = lexical.resolve()
        # Keep reserved evaluation data out of this development workflow.
        if any(resolved == root or resolved.is_relative_to(root) for root in blocked_roots):
            raise ValueError("reserved cohort access is forbidden during these phases")
    # Only explicitly registered exposed evaluations may be scored during development.
    approved = next(
        (
            x
            for x in policy["approved_exposed_evaluations"].values()
            if evaluation.resolve() == (WORKSPACE / x["path"]).resolve()
        ),
        None,
    )
    # Stop if the evaluation is not a registered existing exposed diagnostic.
    if approved is None:
        raise ValueError("evaluation is not a registered existing exposed diagnostic")
    # Stop if the approved exposed evaluation manifest changed.
    if sha256(evaluation / "evaluation_manifest.json") != approved["manifest_sha256"]:
        raise ValueError("approved exposed evaluation manifest changed")
    return policy


# Score one reviewed field while keeping missing and unresolved source values explicit.
def score_item(source_eligible: bool | None, judgment: dict) -> dict:
    assessed = source_eligible is True and judgment["status"] in {"PASS", "FAIL"}
    return {
        "source_eligible": source_eligible,
        "proposal_assessed": assessed,
        "judgment_status": judgment["status"],
        "earned": int(judgment["status"] == "PASS") if source_eligible is True else None,
        "reason_codes": judgment["reason_codes"],
    }


# Score the required course fields and keep overall delivery separate from conditional
# correctness.
def score_course(row: dict, gold: dict) -> dict:
    # Score each field against the reviewed value, including an explicitly confirmed absence.
    items = {
        name: score_item(gold[name]["status"] in RESOLVED_FIELDS, row["fields"][name])
        for name in FIELDS
    }
    items.update(
        {
            name: score_item(None, row["structure"][name])
            for name in ("start_boundary", "end_boundary")
        }
    )
    items["continuation"] = score_item(
        gold["continuation_status"] in CONTINUATIONS, row["structure"]["continuation"]
    )
    extent_eligible = (
        gold["boundary_status"] == "CONFIRMED_BOUNDARY"
        and bool(gold["raw_course_block"])
        and gold["continuation_status"] in CONTINUATIONS
    )
    extent = score_item(extent_eligible, row["structure"]["owned_extent"])
    complete_description = score_item(
        gold["description"]["status"] in RESOLVED_FIELDS,
        {
            "status": "PASS"
            if row["fields"]["description"]["status"] == "PASS"
            and row["structure"]["description_complete"]["status"] == "PASS"
            else "FAIL"
            if row["fields"]["description"]["status"] in {"PASS", "FAIL"}
            else row["fields"]["description"]["status"],
            "reason_codes": list(
                dict.fromkeys(
                    row["fields"]["description"]["reason_codes"]
                    + row["structure"]["description_complete"]["reason_codes"]
                )
            ),
        },
    )
    eligible = sum(items[name]["source_eligible"] is True for name in FIELDS)
    earned = sum(items[name]["earned"] or 0 for name in FIELDS)
    return {
        "dataset_page_id": row["dataset_page_id"],
        "gold_course_id": row["gold_course_id"],
        "code": row["code"],
        "title": row["title"],
        "institution": row.get("institution"),
        "partition": row.get("partition"),
        "pipeline_state": row["pipeline_state"],
        "strict_completion": row["completion"],
        "items": items,
        "confirmed_owned_extent": extent,
        "complete_exact_description": complete_description,
        "expected_continuation": gold["continuation_status"],
        "requires_continuation": gold["continuation_status"] in CONTINUATIONS - {"NONE"},
        "exact_field_score": {
            "earned": earned,
            "source_eligible": eligible,
            "rate": rate(earned, eligible),
            "source_unresolved": len(FIELDS) - eligible,
            "proposal_assessed": sum(items[name]["proposal_assessed"] for name in FIELDS),
        },
        "eight_item_panel_coverage": {
            "source_eligible": sum(x["source_eligible"] is True for x in items.values()),
            "source_unresolved": sum(x["source_eligible"] is False for x in items.values()),
            "not_source_annotated": sum(x["source_eligible"] is None for x in items.values()),
            "proposal_assessed": sum(x["proposal_assessed"] for x in items.values()),
            "total": len(PANEL_ITEMS),
        },
    }


# Count the scored field outcomes without treating source-insufficient cases as negatives.
def item_counts(items: list[dict]) -> dict:
    eligible = sum(x["source_eligible"] is True for x in items)
    correct = sum(x["earned"] == 1 for x in items)
    assessed = sum(x["proposal_assessed"] for x in items)
    return {
        "source_eligible": eligible,
        "correct_delivered": correct,
        "end_to_end_rate": rate(correct, eligible),
        "proposal_assessed": assessed,
        "conditional_rate": rate(correct, assessed),
        "eligible_undelivered_or_unassessed": eligible - assessed,
        "source_unresolved": sum(x["source_eligible"] is False for x in items),
        "not_source_annotated": sum(x["source_eligible"] is None for x in items),
        "judgment_counts": dict(Counter(x["judgment_status"] for x in items)),
    }


# Combine comparable counts and compute only ratios with defined denominators.
def aggregate(rows: list[dict]) -> dict:
    metrics = {name: item_counts([x["items"][name] for x in rows]) for name in PANEL_ITEMS}
    correct = sum(metrics[name]["correct_delivered"] for name in FIELDS)
    eligible = sum(metrics[name]["source_eligible"] for name in FIELDS)
    rates = [
        metrics[name]["end_to_end_rate"]
        for name in FIELDS
        if metrics[name]["end_to_end_rate"] is not None
    ]
    return {
        "courses": len(rows),
        "pipeline_states": dict(Counter(x["pipeline_state"] for x in rows)),
        "field_metrics": metrics,
        "confirmed_owned_extent": item_counts([x["confirmed_owned_extent"] for x in rows]),
        "complete_exact_description": item_counts([x["complete_exact_description"] for x in rows]),
        "required_continuation_status": item_counts(
            [x["items"]["continuation"] for x in rows if x["requires_continuation"]]
        ),
        "five_field_average": {
            "pooled_correct": correct,
            "pooled_source_eligible": eligible,
            "micro_rate": rate(correct, eligible),
            "macro_mean_field_rate": sum(rates) / len(rates) if rates else None,
            "macro_fields_available": len(rates),
            "macro_fields_total": len(FIELDS),
        },
        "strict_completion": {
            name: {
                "correct": sum(x["strict_completion"][name]["correct"] is True for x in rows),
                "source_eligible": sum(
                    x["strict_completion"][name]["source_eligible"] for x in rows
                ),
            }
            for name in ("core", "all_fields")
        },
    }


# Turn the measured field results into an HTML report, escaping all displayed source text.
def render_report(rows: list[dict], summary: dict, *, title: str) -> str:
    esc = html.escape

    # Show the count and its denominator, including cases where no rate can be calculated.
    def fraction(metric):
        d = metric["source_eligible"]
        return (
            f"{metric['correct_delivered']}/{d} ({100 * metric['end_to_end_rate']:.1f}%)"
            if d
            else "NOT ASSESSED"
        )

    # Build an HTML table and escape the text in its cells.
    def table(headings, values):
        return (
            '<div class="scroll"><table><tr>'
            + "".join("<th>" + esc(x) + "</th>" for x in headings)
            + "</tr>"
            + "".join(
                "<tr>" + "".join("<td>" + esc(str(x)) + "</td>" for x in row) + "</tr>"
                for row in values
            )
            + "</table></div>"
        )

    groups = [("Overall", summary["overall"]), *summary["by_partition"].items()]
    metric_rows = []
    for group, item in groups:
        for name, metric in item["field_metrics"].items():
            assessed = metric["proposal_assessed"]
            conditional = f"{metric['correct_delivered']}/{assessed}" if assessed else "N/A"
            metric_rows.append(
                (
                    group,
                    name,
                    fraction(metric),
                    conditional,
                    metric["eligible_undelivered_or_unassessed"],
                    metric["source_unresolved"],
                    metric["not_source_annotated"],
                )
            )
        for name in (
            "confirmed_owned_extent",
            "complete_exact_description",
            "required_continuation_status",
        ):
            m = item[name]
            metric_rows.append(
                (
                    group,
                    name,
                    fraction(m),
                    f"{m['correct_delivered']}/{m['proposal_assessed']}"
                    if m["proposal_assessed"]
                    else "N/A",
                    m["eligible_undelivered_or_unassessed"],
                    m["source_unresolved"],
                    m["not_source_annotated"],
                )
            )
    average_rows = []
    for group, item in groups:
        average = item["five_field_average"]
        micro, macro = average["micro_rate"], average["macro_mean_field_rate"]
        average_rows.append(
            (
                group,
                f"{average['pooled_correct']}/{average['pooled_source_eligible']}",
                f"{100 * micro:.1f}%" if micro is not None else "N/A",
                f"{100 * macro:.1f}%" if macro is not None else "N/A",
                f"{average['macro_fields_available']}/{average['macro_fields_total']}",
                *(
                    f"{item['strict_completion'][k]['correct']}/{item['strict_completion'][k]['source_eligible']}"
                    for k in ("core", "all_fields")
                ),
            )
        )
    course_rows = []
    for row in rows:
        score = row["exact_field_score"]
        states = [row["items"][name]["judgment_status"] for name in PANEL_ITEMS]
        course_rows.append(
            (
                f"{row['code']} — {row['title']}",
                row["partition"],
                row["pipeline_state"],
                *states,
                f"{score['earned']}/{score['source_eligible']}",
                row["strict_completion"]["core"]["correct"],
            )
        )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<style>body{{font:16px/1.5 system-ui;margin:30px auto;max-width:1400px;padding:0 20px}}
body{{color:#182630}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{padding:9px;border:1px solid #ccd5dd;text-align:left;vertical-align:top}}
th{{background:#edf3f8}}h2{{margin-top:28px}}a{{color:#165eaa}}</style><h1>{esc(title)}</h1>
<p>The strict complete-core metric remains the main result: exact code/title/description,
complete description, confirmed exact owned block, physical pages and continuation.
All-fields additionally requires exact credits/prerequisites. These diagnostic scores
do not change extraction, the answer key or the strict evaluator.</p>
<p>Known false-course proposals in the frozen evaluation:
{summary["frozen_false_course_proposals"]}. Field delivery rates are recall diagnostics;
they do not measure false-course precision or prove corpus-wide accuracy.</p>
<h2>Strict completion and five-field averages</h2>
{
        table(
            (
                "Group",
                "Correct / source-eligible field opportunities",
                "Micro field recall",
                "Mean of field recalls",
                "Field types available",
                "Strict core",
                "Strict all fields",
            ),
            average_rows,
        )
    }
<p>The five exact fields are code, title, description, credits and prerequisites. Each earns
one point only for an exact resolved field under the existing whitespace convention.
Micro averages all eligible field opportunities; macro averages the available field-type
recalls equally. Both retain missed records, alignment uncertainty and failed pages as zero
delivery credit. Source-unresolved fields are excluded and counted explicitly. A diagnostic
description value can be exact while completeness remains unconfirmed; complete-exact-description
is reported separately. These averages are diagnostics, not joint complete-course accuracy.</p>
<h2>Field and structure scores</h2>
{
        table(
            (
                "Group",
                "Check",
                "End-to-end",
                "Conditional on assessed proposals",
                "Eligible but undelivered",
                "Source unresolved",
                "No source annotation",
            ),
            metric_rows,
        )
    }
<p>Start/end anchor roles are not separately adjudicated by these imported answers. They stay
NOT_ASSESSED; no fabricated eight-point grade is assigned. Confirmed owned extent is the existing
joint block/page/continuation check. Continuation is status agreement, including NONE. The
required-continuation row isolates actual cross-page/column courses and is not proof of complete
continuation text without the strict owned-block check.</p>
<h2>Per-course diagnostic panel</h2>
{
        table(
            ("Course", "Partition", "Pipeline", *PANEL_ITEMS, "Exact fields", "Strict core"),
            course_rows,
        )
    }
<p>UPSTREAM_NOT_ASSESSED leaves the failure diagnosis upstream; known eligible fields still earn
zero end-to-end delivery credit. SOURCE_UNRESOLVED is not a negative label or an inferred absence.
Source-confirmed absence earns credit only when the parser resolves absence explicitly.
No reserved cohort material or review answers are read. Access remains sealed until all 18
phases are complete and the parser is frozen.</p>
<p><a href="summary.json">Counts and definitions</a> ·
<a href="course_scores.jsonl">Course scores</a> ·
<a href="phase2_manifest.json">Provenance</a></p></html>"""


# Score each reviewed field from an approved cached evaluation and save the detailed results
# and report.
def write_field_scores(evaluation: Path, output: Path) -> dict:
    enforce_policy(evaluation, output)
    diagnosed, provenance = load_exposed_evaluation(evaluation)
    # These are the already-imported exposed answers verified by the loader.
    gold = {x["gold_course_id"]: x for x in read_jsonl(evaluation / "imported_course_truth.jsonl")}
    rows = [score_course(x, gold[x["gold_course_id"]]) for x in diagnosed]
    summary = {
        "overall": aggregate(rows),
        "by_partition": {
            p: aggregate([x for x in rows if x["partition"] == p])
            for p in provenance["summary"]["by_partition"]
        },
        "scope": "EXPOSED_DEVELOPMENT_DIAGNOSTICS",
        "frozen_false_course_proposals": provenance["summary"]["frozen_false_course_proposals"],
        "truth_quality": provenance["summary"]["truth_quality"],
        "field_definition": "Exact whitespace-normalized value and resolved proposal status; "
        "source-reviewed absence must be explicitly resolved by the parser.",
        "average_definition": "Five fields only, equal points per eligible opportunity. Micro "
        "pools "
        "opportunities; macro equally averages available field-type recalls. "
        "Unknown boundaries are excluded, never silently awarded points.",
    }
    # Check the overall and partition totals against the existing strict scores before saving
    # the new field diagnostics.
    for name, group in (("overall", summary["overall"]), *summary["by_partition"].items()):
        frozen = (
            provenance["summary"][name]
            if name == "overall"
            else provenance["summary"]["by_partition"][name]
        )
        for label in ("core", "all_fields"):
            # Field-level reporting must agree with the existing complete-course scores.
            if group["strict_completion"][label] != {
                "correct": frozen[label]["correct"],
                "source_eligible": frozen[label]["eligible"],
            }:
                raise ValueError("Phase 2 changed a strict completion result")
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "course_scores.jsonl", rows)
    write_json(output / "summary.json", summary)
    (output / "index.html").write_text(
        render_report(rows, summary, title="Phase 2 — exact field scoring"), encoding="utf-8"
    )
    result = {
        "schema_version": "course-parser-field-scoring-phase2-v1",
        "status": "DIAGNOSTICS_READY",
        "evaluation_manifest_sha256": provenance["evaluation_manifest_sha256"],
        "evaluation_path": provenance["evaluation_path"],
        "truth_input_sha256": provenance["truth_input_sha256"],
        "phase_execution_policy_sha256": sha256(POLICY_PATH),
        "diagnostic_code_sha256": sha256(Path(__file__)),
        "strict_scoring_changed": False,
        "parser_changed": False,
        "parser_run": False,
        "reserved_cohort_access": "SEALED",
        "reserved_cohort_opened": False,
        "reserved_cohort_answers_used": False,
        "protected_evaluation_opened": False,
        "summary": summary,
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "phase2_manifest.json", result)
    return result


# Read the command-line arguments and run the requested project command.
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_field_scores(args.evaluation, args.output)
    print(args.output.resolve() / "index.html")


if __name__ == "__main__":
    main()
