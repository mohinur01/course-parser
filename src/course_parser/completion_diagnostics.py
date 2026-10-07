# Explain why an extracted course is incomplete. Compare its fields and text boundaries with
# permitted source reviews, keeping missing records and uncertain answers visible.
"""Explain sealed, exposed evaluations without running or changing extraction.

Run with ``python -m course_parser.completion_diagnostics --help``. The existing
scorer remains authoritative. Failed criteria are not independent repair tasks.
Source/OCR/order judgments require a separate source audit, never text equality.
"""

from __future__ import annotations

import argparse
import html
from collections import Counter, defaultdict
from pathlib import Path

from .io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from .learning_evaluation import (
    FIELDS,
    RESOLVED_FIELDS,
    align_headers,
    boundaries_agree,
    canonical,
    complete_course_agrees,
    complete_course_eligible,
    field_values_agree,
)

ALLOWED_PARTITIONS = {"TUNING", "VALIDATION"}  # Exposed DEVELOPMENT subpartitions only.


# Describe whether one required extraction criterion passes, fails or remains unresolved.
def check(
    value: bool | None, reasons: list[str] | None = None, *, unavailable: str = "NOT_ASSESSED"
):
    return {
        "status": unavailable if value is None else "PASS" if value else "FAIL",
        "reason_codes": reasons or [],
    }


# Compare one recovered field with its reviewed source value and retain the failure reason.
def field_check(actual: dict | None, expected: dict, name: str) -> dict:
    gold = expected[name]
    if gold["status"] not in RESOLVED_FIELDS:
        return {**check(None, unavailable="SOURCE_UNRESOLVED"), "value_exact": None}
    if actual is None:
        return {**check(None, unavailable="UPSTREAM_NOT_ASSESSED"), "value_exact": None}
    proposed = actual[name]
    same = field_values_agree(proposed, gold, name)
    reasons = []
    if proposed["status"] not in RESOLVED_FIELDS:
        reasons.append(f"{name.upper()}_FIELD_ABSTAINED")
    if not same:
        a, e = (
            canonical(proposed["raw"], code=name == "code"),
            canonical(gold["raw"], code=name == "code"),
        )
        reason = f"{name.upper()}_TEXT_MISMATCH"
        if gold["status"] == "ABSENT_IN_SOURCE":
            reason = f"{name.upper()}_SOURCE_ABSENCE_NOT_RECOVERED"
        elif name == "code" and a and e:
            if "".join(c for c in a if c.isalnum()) == "".join(c for c in e if c.isalnum()):
                reason = "CODE_PUNCTUATION_MISMATCH"
        elif name == "title" and a and e and a.casefold() == e.casefold():
            reason = "TITLE_CASE_MISMATCH"
        elif name == "description" and a and e:
            if e.startswith(a):
                reason = "DESCRIPTION_TRUNCATED"
            elif a.startswith(e):
                reason = "DESCRIPTION_EXTRA_TEXT"
        reasons.append(reason)
    return {
        **check(same and proposed["status"] in RESOLVED_FIELDS, reasons),
        "value_exact": same,
        "expected_status": gold["status"],
        "proposed_status": proposed["status"],
        "expected_raw": gold["raw"],
        "proposed_raw": proposed["raw"],
    }


# Keep downstream checks unassessed when no uniquely aligned record exists.
def diagnose_course(
    expected: dict,
    actual: dict | None,
    *,
    candidate: dict | None = None,
    decision: dict | None = None,
    pipeline_state: str = "RECORD_EMITTED",
) -> dict:
    """Keep downstream checks unassessed when no uniquely aligned record exists."""
    fields = {name: field_check(actual, expected, name) for name in FIELDS}
    description_complete = None
    raw_exact = extent_exact = continuation = physical_extent = boundary_confirmed = None
    if actual is not None:
        if expected["description"]["status"] in RESOLVED_FIELDS:
            description_complete = (
                expected["description"]["status"] == "ABSENT_IN_SOURCE"
                or actual["description_status"] == "COMPLETE_DESCRIPTION"
            )
        if expected["boundary_status"] == "CONFIRMED_BOUNDARY":
            raw_exact = canonical(actual["raw_course_block"]) == canonical(
                expected["raw_course_block"]
            )
            physical_extent = actual["physical_pages"] == expected["physical_pages"]
            boundary_confirmed = actual["boundary_status"] == "CONFIRMED_BOUNDARY"
            if expected["continuation_status"] in {
                "NONE",
                "CONFIRMED_CROSS_COLUMN",
                "CONFIRMED_CROSS_PAGE",
            }:
                continuation = actual["continuation_status"] == expected["continuation_status"]
                extent_exact = boundaries_agree(actual, expected)
    extent_reasons = []
    for value, reason in (
        (raw_exact, "OWNED_BLOCK_TEXT_MISMATCH"),
        (physical_extent, "PHYSICAL_PAGE_EXTENT_MISMATCH"),
        (boundary_confirmed, "BOUNDARY_UNCONFIRMED"),
        (continuation, "CONTINUATION_STATUS_MISMATCH"),
    ):
        if value is False:
            extent_reasons.append(reason)
    structure = {
        "raw_block_exact": check(
            raw_exact, ["OWNED_BLOCK_TEXT_MISMATCH"] if raw_exact is False else []
        ),
        "owned_extent": check(extent_exact, extent_reasons),
        "description_complete": check(
            description_complete,
            ["DESCRIPTION_COMPLETENESS_UNCONFIRMED"] if description_complete is False else [],
        ),
        "continuation": check(
            continuation, ["CONTINUATION_STATUS_MISMATCH"] if continuation is False else []
        ),
        # These need image/geometry or channel adjudication, not exact block text.
        **{
            name: check(None)
            for name in (
                "start_boundary",
                "end_boundary",
                "reading_order",
                "ocr",
                "source_text_complete",
                "token_ownership",
                "metadata_separation",
            )
        },
    }
    # Bundle the exact/resolved description and its completeness into one gate.
    # Extent includes block, page coverage, boundary confirmation and continuation.
    core_checks = {name: fields[name] for name in ("code", "title", "description")}
    if description_complete is False:
        core_checks["description"] = {
            **fields["description"],
            "status": "FAIL",
            "reason_codes": fields["description"]["reason_codes"]
            + ["DESCRIPTION_COMPLETENESS_UNCONFIRMED"],
        }
    core_checks["owned_extent"] = structure["owned_extent"]
    all_checks = {**core_checks, **{name: fields[name] for name in ("credits", "prerequisites")}}
    completion = {}
    for label, checks, all_fields in (
        ("core", core_checks, False),
        ("all_fields", all_checks, True),
    ):
        eligible = complete_course_eligible(expected, all_fields=all_fields)
        failed = [name for name, value in checks.items() if value["status"] == "FAIL"]
        assessed = (
            eligible
            and actual is not None
            and all(value["status"] in {"PASS", "FAIL"} for value in checks.values())
        )
        correct = bool(
            actual is not None and complete_course_agrees(actual, expected, all_fields=all_fields)
        )
        # The diagnostic completion checks must agree with the saved evaluator.
        if assessed and correct != (not failed):
            raise ValueError("completion gates disagree with frozen scorer")
        completion[label] = {
            "source_eligible": eligible,
            "correct": correct if eligible else None,
            "failed_criteria": failed if assessed else None,
            "failed_criteria_count": len(failed) if assessed else None,
            "distance_status": "ASSESSED"
            if assessed
            else "SOURCE_UNRESOLVED"
            if not eligible
            else pipeline_state,
        }
    if pipeline_state == "ALIGNMENT_AMBIGUOUS":
        detection = classification = check(None, ["ALIGNMENT_AMBIGUOUS"])
    elif pipeline_state == "PARSER_STAGE_FAILED":
        detection = classification = check(None, ["PARSER_STAGE_FAILED"])
    else:
        detection = check(candidate is not None, [] if candidate else ["NO_ALIGNED_HEADER"])
        classification = check(
            decision["status"] == "INDEPENDENT_COURSE" if decision else None,
            []
            if decision and decision["status"] == "INDEPENDENT_COURSE"
            else ["ENTRY_NOT_CLASSIFIED_AS_INDEPENDENT"]
            if decision
            else [],
        )
    return {
        "dataset_page_id": expected["dataset_page_id"],
        "gold_course_id": expected["gold_course_id"],
        "code": expected["code"]["raw"],
        "title": expected["title"]["raw"],
        "physical_page": expected["header_physical_page"],
        "pipeline_state": pipeline_state,
        "candidate_id": candidate["candidate_id"] if candidate else None,
        "candidate_status": candidate["status"] if candidate else None,
        "entry_status": decision["status"] if decision else None,
        "entry_reason_codes": decision.get("reason_codes", []) if decision else [],
        "detection": detection,
        "classification": classification,
        "fields": fields,
        "structure": structure,
        "completion": completion,
    }


# Combine the individual diagnostic results into counts with explicit eligible denominators.
def summarize(rows: list[dict]) -> dict:
    counts = {
        "courses": len(rows),
        "pipeline_states": dict(Counter(x["pipeline_state"] for x in rows)),
        "raw_block_exact": sum(x["structure"]["raw_block_exact"]["status"] == "PASS" for x in rows),
        "confirmed_extent_exact": sum(
            x["structure"]["owned_extent"]["status"] == "PASS" for x in rows
        ),
        "description_value_exact": sum(
            x["fields"]["description"]["value_exact"] is True for x in rows
        ),
        "field_checks": {
            name: dict(Counter(x["fields"][name]["status"] for x in rows)) for name in FIELDS
        },
        "detection_checks": dict(Counter(x["detection"]["status"] for x in rows)),
        "classification_checks": dict(Counter(x["classification"]["status"] for x in rows)),
    }
    for label in ("core", "all_fields"):
        distances = Counter()
        for row in rows:
            item = row["completion"][label]
            if item["failed_criteria_count"] is not None:
                distances[str(min(item["failed_criteria_count"], 3))] += 1
        counts[label] = {
            "eligible": sum(x["completion"][label]["source_eligible"] for x in rows),
            "correct": sum(x["completion"][label]["correct"] is True for x in rows),
            "failed_criteria_buckets": {k: distances[k] for k in ("0", "1", "2", "3")},
            "failed_criteria_counts_assessed_records": dict(
                Counter(
                    name
                    for row in rows
                    for name in row["completion"][label]["failed_criteria"] or []
                )
            ),
            "not_distance_assessed": dict(
                Counter(
                    x["completion"][label]["distance_status"]
                    for x in rows
                    if x["completion"][label]["failed_criteria_count"] is None
                )
            ),
        }
    joint = [
        x
        for x in rows
        if x["structure"]["owned_extent"]["status"] == "PASS"
        and x["fields"]["description"]["value_exact"] is True
    ]
    counts["confirmed_extent_and_description_value_exact"] = {
        "count": len(joint),
        "courses": [
            {
                "gold_course_id": x["gold_course_id"],
                "code": x["code"],
                "remaining_core_criteria": x["completion"]["core"]["failed_criteria"],
                "remaining_all_fields_criteria": x["completion"]["all_fields"]["failed_criteria"],
            }
            for x in joint
        ],
    }
    return counts


# Use only already-imported answers from a verified DEVELOPMENT evaluation.
def load_exposed_evaluation(evaluation: Path) -> tuple[list[dict], dict]:
    """Use only already-imported answers from a verified DEVELOPMENT evaluation."""
    manifest_path = evaluation / "evaluation_manifest.json"
    manifest = read_json(manifest_path)
    # Accept only saved evaluations that meet the exposed development-data rules.
    if (
        manifest.get("schema_version") != "course-parser-learning-evaluation-v2"
        or manifest.get("status") != "DIAGNOSTIC_READY"
        or manifest.get("scope")
        != "EXPOSED_DEVELOPMENT_DIAGNOSTIC_NOT_FINAL_GENERALIZATION_ACCURACY"
        or manifest.get("protected_evaluation_opened") is not False
        or not set(manifest.get("partitions_scored", [])) <= ALLOWED_PARTITIONS
    ):
        raise ValueError("only sealed, exposed DEVELOPMENT evaluations are accepted")
    verify_files(evaluation, manifest["file_sha256"])
    # Stop if the frozen evaluator code differs.
    if (
        sha256(Path(__file__).with_name("learning_evaluation.py"))
        != manifest["evaluation_code_sha256"]
    ):
        raise ValueError("frozen evaluator code differs")
    predictions, pilot = Path(manifest["prediction_path"]), Path(manifest["pilot_path"])
    for root, name, hash_key in (
        (predictions, "prediction_manifest.json", "prediction_manifest_sha256"),
        (pilot, "pilot_manifest.json", "pilot_manifest_sha256"),
    ):
        # Stop if the evaluation input manifest differs.
        if sha256(root / name) != manifest[hash_key]:
            raise ValueError("evaluation input manifest differs")
    pred = read_json(predictions / "prediction_manifest.json")
    pilot_manifest = read_json(pilot / "pilot_manifest.json")
    # Stop if the prediction or pilot is outside the exposed diagnostic contract.
    if (
        pred["source_truth_read"] is not False
        or pred["protected_evaluation_opened"] is not False
        or pilot_manifest.get("split") != "DEVELOPMENT"
        or pilot_manifest.get("protected_evaluation_opened") is not False
        or pred["pilot_manifest_sha256"] != manifest["pilot_manifest_sha256"]
    ):
        raise ValueError("prediction or pilot is outside the exposed diagnostic contract")
    verify_files(predictions, pred["file_sha256"])
    verify_files(pilot, pilot_manifest["file_sha256"])
    selected = {x["dataset_page_id"]: x for x in read_jsonl(pilot / "selected_pages.jsonl")}
    # Protected or fresh held-out cohort cannot enter these diagnostics.
    if any(
        x["split"] != "DEVELOPMENT" or x["learning_partition"] not in ALLOWED_PARTITIONS
        for x in selected.values()
    ):
        raise ValueError("protected or fresh held-out cohort cannot enter these diagnostics")
    page_metrics = {x["dataset_page_id"]: x for x in read_jsonl(evaluation / "page_metrics.jsonl")}
    proposals = {x["dataset_page_id"]: x for x in read_jsonl(predictions / "page_proposals.jsonl")}
    records = defaultdict(dict)
    for row in read_jsonl(predictions / "course_proposals.jsonl"):
        if row["scope"] == "CENTRAL":
            cid = row["course"]["header_candidate_id"]
            # Reject duplicate course proposal.
            if cid in records[row["dataset_page_id"]]:
                raise ValueError("duplicate course proposal")
            records[row["dataset_page_id"]][cid] = row["course"]
    gold = defaultdict(list)
    for course in read_jsonl(evaluation / "imported_course_truth.jsonl"):
        if course["scope"] == "HEADER_STARTS_ON_CENTRAL_PAGE" and page_metrics[
            course["dataset_page_id"]
        ]["status"] in {"SCORED_DEVELOPMENT", "PARSER_STAGE_FAILED"}:
            gold[course["dataset_page_id"]].append(course)
    rows = []
    for page_id, courses in gold.items():
        item, proposal = selected[page_id], proposals[page_id]
        candidates = [
            x
            for x in proposal.get("candidates", [])
            if (x["physical_page"] == item["central_physical_page"] and x["status"] != "REJECTED")
        ]
        matched, ambiguous = align_headers(candidates, courses)
        by_gold = {gid: cid for cid, gid in matched.items()}
        by_candidate = {x["candidate_id"]: x for x in candidates}
        decisions = {x["candidate_id"]: x for x in proposal.get("decisions", [])}
        for expected in courses:
            cid = by_gold.get(expected["gold_course_id"])
            actual = records[page_id].get(cid)
            ambiguous_gold = any(align_headers([by_candidate[a]], [expected])[0] for a in ambiguous)
            state = (
                "PARSER_STAGE_FAILED"
                if page_metrics[page_id]["status"] == "PARSER_STAGE_FAILED"
                else "ALIGNMENT_AMBIGUOUS"
                if ambiguous_gold
                else "RECORD_EMITTED"
                if actual
                else "ENTRY_WITHHELD"
                if cid
                else "NO_ALIGNED_HEADER"
            )
            if state in {"PARSER_STAGE_FAILED", "ALIGNMENT_AMBIGUOUS"}:
                actual, cid = None, None
            rows.append(
                {
                    **diagnose_course(
                        expected,
                        actual,
                        candidate=by_candidate.get(cid),
                        decision=decisions.get(cid),
                        pipeline_state=state,
                    ),
                    "institution": item["institution"],
                    "partition": item["learning_partition"],
                    "source_pdf_checksum": item["source_pdf_checksum"],
                    "evaluation_page_status": page_metrics[page_id]["status"],
                }
            )
    summary = {
        "overall": summarize(rows),
        "by_partition": {
            p: summarize([x for x in rows if x["partition"] == p])
            for p in manifest["partitions_scored"]
        },
    }
    # A diagnostic extension must explain existing outcomes, never change them.
    for key, group in (("overall", summary["overall"]), *summary["by_partition"].items()):
        original = manifest["overall"] if key == "overall" else manifest["by_partition"][key]
        for diagnostic_key, metric_key in (
            ("raw_block_exact", "block_exact_matched"),
            ("confirmed_extent_exact", "confirmed_extent_exact_matched"),
            ("description_value_exact", "description_exact_matched"),
        ):
            # Each diagnostic total must match the corresponding saved evaluation total.
            if group[diagnostic_key] != original.get(metric_key, 0):
                raise ValueError(f"diagnostics disagree with frozen {metric_key}")
        for label, prefix in (("core", "complete_content"), ("all_fields", "complete_all_fields")):
            # The complete-course totals must match the saved evaluation.
            if any(
                group[label][name] != original.get(f"{prefix}_{name}", 0)
                for name in ("correct", "eligible")
            ):
                raise ValueError("diagnostics disagree with frozen complete-course metrics")
    summary["frozen_false_course_proposals"] = manifest["overall"]["false_course_proposals"]
    summary["truth_quality"] = manifest["truth_quality"]
    summary["criteria_definition"] = (
        "Core has four gates: exact resolved code; exact resolved title; exact resolved and "
        "complete description (or source-confirmed absence); confirmed exact owned block, "
        "page extent and continuation. All-fields adds exact resolved credits/prerequisites."
    )
    summary["distance_note"] = (
        "Bucket 3 means 3+ failed criteria, not repair-effort or independent-field estimates. "
        "Missing records, stage failures, ambiguous alignment and source-unresolved criteria "
        "are counted separately; eligible missed courses remain in recall denominators."
    )
    return rows, {
        "evaluation_path": str(evaluation.resolve()),
        "evaluation_manifest_sha256": sha256(manifest_path),
        "prediction_manifest_sha256": manifest["prediction_manifest_sha256"],
        "truth_input_sha256": manifest["truth_input_sha256"],
        "summary": summary,
    }


# Compare the saved exposed predictions with their source reviews and write the
# completion-failure report.
def write_diagnostics(evaluation: Path, output: Path) -> dict:
    rows, evidence = load_exposed_evaluation(evaluation)
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "course_matrix.jsonl", rows)
    write_json(output / "summary.json", evidence["summary"])
    columns = ("code", "title", "description", "credits", "prerequisites")
    rendered = []
    for row in rows:
        core = row["completion"]["core"]
        failures = core["failed_criteria"]
        cells = [
            f"<td>{html.escape(str(row['code']))}<small>{html.escape(row['institution'])}"
            f" · {html.escape(row['partition'])}</small></td>",
            f"<td>{html.escape(row['pipeline_state'])}</td>",
        ]
        for value in [
            row["detection"],
            row["classification"],
            *(row["fields"][k] for k in columns),
            row["structure"]["owned_extent"],
            row["structure"]["description_complete"],
        ]:
            cells.append(
                f'<td title="{html.escape(", ".join(value["reason_codes"]), quote=True)}">'
                f"{html.escape(value['status'])}</td>"
            )
        remaining = ", ".join(failures) if failures is not None else core["distance_status"]
        cells.append(f"<td>{html.escape(remaining)}</td>")
        rendered.append("<tr>" + "".join(cells) + "</tr>")
    document = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Course completion diagnostics</title><style>
body{font:16px/1.5 system-ui;margin:24px;color:#17212b}
table{border-collapse:collapse;font-size:13px}
td,th{padding:8px;border:1px solid #ccd3db;text-align:left;vertical-align:top}small{display:block}
</style><h1>Course completion diagnostics</h1>
<p>Sealed, exposed development results. No extraction was run or changed. Exact fields use the
frozen scorer's whitespace convention; punctuation and title case are retained.</p>
<p>Core: exact resolved code/title/description, complete description, confirmed exact owned block,
page extent and continuation. All-fields additionally requires credits/prerequisites, including
source-confirmed absence. Missing records and failed pages remain in recall denominators.</p>
<p>Failed core criteria are diagnostic gates, not independent fields or repair-effort estimates.
Missing records, ambiguous alignment and unresolved source answers have no distance count.
Start/end boundary, reading order, OCR, source-text completeness, token ownership and metadata
separation are NOT_ASSESSED here: proposal equality cannot establish those source judgments.
Reason codes are available by hovering over failed checks and in the JSONL matrix.</p>
<p><a href="summary.json">Counts, distances and joint overlap</a> ·
<a href="course_matrix.jsonl">Full matrix and reason codes</a></p><table><tr>"""
    headings = (
        "Course",
        "Pipeline",
        "Header detected",
        "Independent course",
        *columns,
        "Confirmed extent",
        "Complete description",
        "Remaining core criteria",
    )
    document += "".join(f"<th>{html.escape(x)}</th>" for x in headings)
    document += "</tr>" + "".join(rendered) + "</table></html>"
    (output / "index.html").write_text(document, encoding="utf-8")
    manifest = {
        "schema_version": "course-parser-completion-diagnostics-v1",
        "status": "FROZEN_EVALUATION_EXPLAINED",
        **evidence,
        "diagnostic_code_sha256": sha256(Path(__file__)),
        "parser_run": False,
        "protected_evaluation_opened": False,
        "fresh_holdout_opened": False,
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "diagnostics_manifest.json", manifest)
    return manifest


# Read the command-line arguments and run the requested project command.
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    write_diagnostics(args.evaluation, args.output)
    print(f"Completion diagnostics: {args.output.resolve() / 'index.html'}")


if __name__ == "__main__":
    main()
