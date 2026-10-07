# Build a report explaining the previously measured course-completion failures and the
# suggestions made about them. Reuse saved evaluations without changing the parser or source
# answers.
"""Evaluate the screenshot suggestions using previously sealed results only."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from course_parser.completion_diagnostics import write_diagnostics
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
EXPERIMENT = WORKSPACE / "artifacts/validation-transfer-v1"
esc = html.escape


# Build one HTML table row with safely escaped cell text.
def cells(values):
    return "<tr>" + "".join(f"<td>{esc(str(x))}</td>" for x in values) + "</tr>"


# Build an HTML table and escape the text in its cells.
def table(headings, rows):
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join(f"<th>{esc(x)}</th>" for x in headings)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


# Count the criteria still preventing the exposed records from being complete.
def remaining(row, label):
    item = row["completion"][label]
    if item["failed_criteria"] is None:
        return item["distance_status"]
    return ", ".join(item["failed_criteria"]) or "COMPLETE"


# Build a report explaining the previously measured course-completion failures and the
# suggestions made about them.
def build(output: Path):
    freeze_path = EXPERIMENT / "release/freeze_manifest.json"
    freeze = read_json(freeze_path)
    verify_files(WORKSPACE, freeze["file_sha256"])
    audit_root = EXPERIMENT / "audit"
    audit_manifest = read_json(audit_root / "audit_manifest.json")
    verify_files(audit_root, audit_manifest["file_sha256"])
    output.mkdir(parents=True, exist_ok=False)
    releases = {}
    for name, relative in (
        ("original-before", "artifacts/progress-measurement-20261006/original_current"),
        ("original-current", "artifacts/validation-transfer-v1/release/original-evaluation"),
        ("expanded-before", "artifacts/table-ownership-v1/release/measurement/after"),
        ("expanded-current", "artifacts/validation-transfer-v1/release/expanded-evaluation"),
    ):
        releases[name] = write_diagnostics(WORKSPACE / relative, output / name)
    before_rows = read_jsonl(output / "original-before/course_matrix.jsonl")
    after_rows = {
        x["gold_course_id"]: x for x in read_jsonl(output / "original-current/course_matrix.jsonl")
    }
    # Before and after results must contain the same reviewed courses.
    if {x["gold_course_id"] for x in before_rows} != set(after_rows):
        raise ValueError("paired course cohorts differ")
    # Before and after results must use exactly the same source-review answers.
    if (
        releases["original-before"]["truth_input_sha256"]
        != releases["original-current"]["truth_input_sha256"]
    ):
        raise ValueError("paired source answers differ")
    audit_rows = {
        x["gold_course_id"]: x for x in read_jsonl(audit_root / "course_failure_matrix_52.jsonl")
    }
    paired = [
        {
            "gold_course_id": row["gold_course_id"],
            "before": row,
            "current": after_rows[row["gold_course_id"]],
            "source_audit_before": audit_rows[row["gold_course_id"]],
            "source_audit_current": "NOT_REAUDITED_BY_THIS_DIAGNOSTIC_EXTENSION",
        }
        for row in before_rows
    ]
    write_jsonl(output / "paired_source_diagnostics_52.jsonl", paired)
    suggestions = [
        {
            "suggestion": "Freeze before diagnosis; expose the old 15; reserve new institutions",
            "decision": "ALREADY_IMPLEMENTED",
            "reason": "The source audit and reservation preceded parser changes. The final parser "
            "is archived. New held-out course outcomes remain unopened.",
        },
        {
            "suggestion": "Separate detection, independent-course classification and exact fields",
            "decision": "IMPLEMENTED_DIAGNOSTIC_EXTENSION",
            "reason": "The new matrix separates aligned header, candidate support, entry decision, "
            "emitted record, exact field value, field resolution and completion gates.",
        },
        {
            "suggestion": "Give failures reason codes",
            "decision": "IMPLEMENTED_WITH_EVIDENCE_LIMITS",
            "reason": "Deterministic mismatches and abstentions get reason codes; the existing "
            "source audit retains cause-specific observations. OCR, ownership and "
            "ordering causes are not guessed from string differences.",
        },
        {
            "suggestion": "Count 0, 1, 2 and 3+ fields away",
            "decision": "IMPLEMENTED_AS_FAILED_COMPLETION_CRITERIA",
            "reason": "Core and all-fields counts are separate. Missing records, ambiguous "
            "alignments, stage failures and unresolved source answers have no distance "
            "count. Several criteria can fail from one upstream cause.",
        },
        {
            "suggestion": "Find exact-boundary / exact-description overlap and prioritize causes",
            "decision": "IMPLEMENTED_AND_PREVIOUS_FIXES_SUPPORTED",
            "reason": "The pre-change confirmed-extent / exact-description-value overlap is five, "
            "not six. All five failed code: four punctuation losses and one "
            "source-observed OCR defect. Identifier fixes were already made.",
        },
        {
            "suggestion": "Mark source text, OCR, ordering and every boundary binary pass/fail",
            "decision": "REQUIRES_SOURCE_AUDIT_OR_NOT_ASSESSED",
            "reason": "Exact block equality does not independently certify source coverage, "
            "reading order, start/end anchors or token ownership. The pre-change "
            "source audit is retained separately; current unreviewed judgments stay "
            "NOT_ASSESSED. Missing or ambiguous evidence must not become a binary guess.",
        },
        {
            "suggestion": "Make code/title the next priority if the overlap supports it",
            "decision": "CONDITIONAL_ADVICE_NOW_PARTLY_STALE",
            "reason": "The old zero-of-fifteen core score is now ten-of-fifteen. Code errors were "
            "a demonstrated priority. The remaining failures include absent records, "
            "unconfirmed boundaries and source-quote differences. Further extraction "
            "tuning is deferred until the frozen parser is independently evaluated.",
        },
    ]
    write_json(output / "suggestion_decisions.json", suggestions)
    summaries = {name: item["summary"] for name, item in releases.items()}
    old = summaries["original-before"]["by_partition"]["VALIDATION"]
    current = summaries["original-current"]["by_partition"]["VALIDATION"]
    measurement_rows = []
    for cohort, group, prefix in (
        ("Old 15 → exposed diagnostic", "by_partition", "VALIDATION"),
        ("Other 37 tuning courses", "by_partition", "TUNING"),
        ("Original 52, core", "overall", None),
        ("Original 52, all fields", "overall", None),
    ):
        label = "all_fields" if "all fields" in cohort else "core"
        pair = []
        for name in ("original-before", "original-current"):
            s = summaries[name][group][prefix] if prefix else summaries[name][group]
            pair.append(f"{s[label]['correct']}/{s[label]['eligible']}")
        measurement_rows.append(cells((cohort, *pair)))
    for label in ("core", "all_fields"):
        pair = [
            summaries[name]["overall"][label] for name in ("expanded-before", "expanded-current")
        ]
        measurement_rows.append(
            cells((f"Expanded 128, {label}", *(f"{x['correct']}/{x['eligible']}" for x in pair)))
        )
    distances = []
    for label, heading in (("core", "Core criteria"), ("all_fields", "All-fields criteria")):
        for bucket in ("0", "1", "2", "3"):
            distances.append(
                cells(
                    (
                        heading,
                        "3+" if bucket == "3" else bucket,
                        old[label]["failed_criteria_buckets"][bucket],
                        current[label]["failed_criteria_buckets"][bucket],
                    )
                )
            )
        distances.append(
            cells(
                (
                    heading,
                    "Upstream / alignment (no distance)",
                    sum(old[label]["not_distance_assessed"].values()),
                    sum(current[label]["not_distance_assessed"].values()),
                )
            )
        )
    diagnostic_rows = []
    for row in paired:
        b, a = row["before"], row["current"]
        if b["partition"] == "VALIDATION":
            diagnostic_rows.append(
                cells(
                    (
                        f"{b['code']} — {b['title']}",
                        b["pipeline_state"],
                        a["pipeline_state"],
                        remaining(b, "core"),
                        remaining(a, "core"),
                        remaining(a, "all_fields"),
                    )
                )
            )
    joint_rows = []
    for row in old["confirmed_extent_and_description_value_exact"]["courses"]:
        source = audit_rows[row["gold_course_id"]]
        joint_rows.append(
            cells(
                (
                    row["code"],
                    ", ".join(row["remaining_core_criteria"]),
                    ", ".join(source["failure_classes"]),
                )
            )
        )
    cohort = summaries["expanded-current"]["overall"]
    parser_gaps = table(
        ("Current expanded pipeline state", "Courses"),
        [cells((k, v)) for k, v in cohort["pipeline_states"].items()],
    )
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Course parser — evaluating the diagnostic suggestions</title><style>
body{{font:16px/1.55 system-ui;color:#17212b;max-width:1350px;margin:32px auto;padding:0 20px}}
h1{{font-size:30px}}h2{{margin-top:30px;font-size:23px}}a{{color:#155aad}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:10px;
border:1px solid #d1d8e0;vertical-align:top}}th{{background:#edf3f8}}.scroll{{overflow-x:auto}}
.note{{padding:16px;background:#edf5f3;border-left:4px solid #23816c}}
</style><h1>Most suggestions are appropriate; the diagnosis now needs current numbers</h1>
<p class="note">Implemented the missing diagnostic layer. No parser rules, reviewed answers,
matching vocabulary or scoring definitions changed in this turn. All supplemental counts agree
with the sealed evaluations. The frozen parser and new held-out course outcomes remain intact.</p>
<h2>Decisions on the suggestions</h2>
{
        table(
            ("Suggestion", "Decision", "Evidence / qualification"),
            [cells((x["suggestion"], x["decision"], x["reason"])) for x in suggestions],
        )
    }
<h2>The overlap question has an actual answer</h2>
<p>Before the changes, the old diagnostic set had {old["raw_block_exact"]} exact raw blocks,
{old["confirmed_extent_exact"]} confirmed exact extents, and {old["description_value_exact"]}
exact description values. Their confirmed-extent / description-value intersection was
<strong>{old["confirmed_extent_and_description_value_exact"]["count"]} courses</strong>.
All five failed code. Four lost printed punctuation; one had an image-confirmed OCR identifier
error. Thus the earlier identifier work had direct source evidence.</p>
{
        table(
            ("Course", "Remaining core criterion before fixes", "Pre-change source diagnosis"),
            joint_rows,
        )
    }
<p>The sixth exact description belonged to an unconfirmed extent and carried a partial status.
Raw value equality alone does not establish complete assembly. Current confirmed-extent /
exact-description-value overlap is {
        current["confirmed_extent_and_description_value_exact"]["count"]
    };
one of these still fails code. The other ten are core-complete.</p>
<h2>Measured extraction results are unchanged by this diagnostic extension</h2>
{
        table(
            ("Cohort / measure", "Before earlier parser fixes", "Frozen current parser"),
            measurement_rows,
        )
    }
<p>Core jointly requires exact resolved code, title and description, complete description,
confirmed exact owned block, page extent and continuation. All-fields also requires exact
credits and prerequisites, including reviewed absence. Current all-fields completion remains
<strong>0/15 on the old diagnostic set</strong>; it must not be confused with 10/15 core completion.
Original all-fields eligibility is 38; expanded eligibility is 97. The expanded 128 includes
the original 52. None of these exposed samples proves held-out generalization.</p>
<h2>How far are the old 15 from passing?</h2>
{table(("Measure", "Failed criteria", "Before earlier fixes", "Frozen current parser"), distances)}
<p>These are failed completion gates, not independent fields or effort estimates. Core has
four gates: code; title; exact resolved complete description; confirmed owned extent including
block text, pages and continuation. All-fields adds credits and prerequisites. Missing records
and unresolved alignment do not receive fabricated downstream field failures. Eligible misses
and failed pages still remain in end-to-end recall denominators.</p>
<h2>Every old diagnostic course, before and current</h2>
{
        table(
            (
                "Course",
                "Before pipeline",
                "Current pipeline",
                "Before remaining core criteria",
                "Current remaining core criteria",
                "Current remaining all-fields criteria",
            ),
            diagnostic_rows,
        )
    }
<p><a href="original-before/index.html">All 52 before: detection, classification and fields</a> ·
<a href="original-current/index.html">All 52 current: detection, classification and fields</a> ·
<a href="paired_source_diagnostics_52.jsonl">Paired checks and pre-change source audit</a></p>
<h2>The wider sample prevents a misleading conclusion</h2>
<p>Current expanded core completion is {cohort["core"]["correct"]}/{cohort["core"]["eligible"]}.
Only {cohort["pipeline_states"]["RECORD_EMITTED"]} of 128 reviewed courses have uniquely aligned
records. Eleven gold courses sit on a reviewed page with a parser-stage failure. Four courses
have ambiguous alignment; those are not proven absent headers. The expanded sample also retains
one known description-contamination case. Missing/withheld entries and stage failures remain
material bottlenecks even though the 15-course identifier problem improved.</p>
{parser_gaps}
<p><a href="expanded-before/index.html">Expanded 128 before</a> ·
<a href="expanded-current/index.html">Expanded 128 current</a></p>
<h2>What remains source-based</h2>
<p>The original source audit already covers start/end boundaries, continuation, metadata
separation, reading order, OCR and ownership for the 52. That audit describes the pre-change
parser; this extension does not recertify current source behavior. Current exact block checks
are comparisons to reviewed answers. They do not independently prove complete source-text
coverage, reading order, OCR accuracy or token ownership. Those judgments stay NOT_ASSESSED
in the automated matrix unless separately source-audited.</p>
<p>The next useful step is independent review of the 34 reserved windows from six institution
families, including the issue/year identity check, followed by one evaluation of the archived
frozen parser. No new held-out course images, text, answers or predictions were opened for
this extension. No CRF or other model was trained. Reused source reviews are not independent
gold certification.</p></html>"""
    (output / "index.html").write_text(document, encoding="utf-8")
    verify_files(WORKSPACE, freeze["file_sha256"])
    write_json(
        output / "report_manifest.json",
        {
            "schema_version": "course-parser-completion-suggestions-report-v1",
            "status": "DIAGNOSTICS_EXTENDED_PARSER_UNCHANGED",
            "parser_freeze_manifest_sha256": sha256(freeze_path),
            "frozen_files_verified_before_and_after": True,
            "source_audit_manifest_sha256": sha256(audit_root / "audit_manifest.json"),
            "new_files_sha256": {
                str(p.relative_to(WORKSPACE)): sha256(p)
                for p in (
                    WORKSPACE / "src/course_parser/completion_diagnostics.py",
                    WORKSPACE / "tests/test_completion_diagnostics.py",
                    Path(__file__),
                )
            },
            "suggestion_screenshots": "User attachments dated 2026-10-06 at 18:43:02–18:43:31",
            "parser_changed": False,
            "score_definition_changed": False,
            "source_answers_changed": False,
            "fresh_holdout_opened": False,
            "protected_calibration_test_opened": False,
            "file_sha256": artifact_hashes(output),
        },
    )
    print(output / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=WORKSPACE / "artifacts/completion-diagnostics-v1"
    )
    build(parser.parse_args().output)
