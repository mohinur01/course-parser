# Build field-by-field diagnostic reports from registered exposed evaluations. Reuse the same
# source answers and strict completion definitions for the paired comparisons.
"""Build Phase 2 reports from the registered exposed evaluations only."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from course_parser.field_scoring import FIELDS, POLICY_PATH, WORKSPACE, write_field_scores
from course_parser.io import artifact_hashes, read_json, sha256, verify_files, write_json


# Build field-by-field diagnostic reports from registered exposed evaluations.
def build(output: Path):
    policy = read_json(POLICY_PATH)
    freeze_path = WORKSPACE / "artifacts/validation-transfer-v1/release/freeze_manifest.json"
    freeze = read_json(freeze_path)
    verify_files(WORKSPACE, freeze["file_sha256"])
    # Preflight outputs and inputs before creating any release directories.
    from course_parser.field_scoring import enforce_policy

    for name, item in policy["approved_exposed_evaluations"].items():
        enforce_policy(WORKSPACE / item["path"], output / name)
    output.mkdir(parents=True, exist_ok=False)
    releases = {
        name: write_field_scores(WORKSPACE / item["path"], output / name)
        for name, item in policy["approved_exposed_evaluations"].items()
    }
    for prefix in ("original", "expanded"):
        before, current = releases[prefix + "-before"], releases[prefix + "-current"]
        # Before and after results must use exactly the same source-review answers.
        if before["truth_input_sha256"] != current["truth_input_sha256"]:
            raise ValueError("paired source answers differ")
    old = releases["original-before"]["summary"]["by_partition"]["VALIDATION"]
    current = releases["original-current"]["summary"]["by_partition"]["VALIDATION"]
    rows = []
    for name in FIELDS:
        pair = [x["field_metrics"][name] for x in (old, current)]
        rows.append((name, *(f"{x['correct_delivered']}/{x['source_eligible']}" for x in pair)))
    for name in (
        "confirmed_owned_extent",
        "complete_exact_description",
        "required_continuation_status",
    ):
        rows.append(
            (
                name,
                *(
                    f"{x[name]['correct_delivered']}/{x[name]['source_eligible']}"
                    for x in (old, current)
                ),
            )
        )
    for label in ("core", "all_fields"):
        rows.append(
            (
                f"Strict {label}",
                *(
                    f"{x['strict_completion'][label]['correct']}/"
                    f"{x['strict_completion'][label]['source_eligible']}"
                    for x in (old, current)
                ),
            )
        )
    for name in ("micro_rate", "macro_mean_field_rate"):
        rows.append(
            (name, *(f"{100 * x['five_field_average'][name]:.1f}%" for x in (old, current)))
        )
    esc = html.escape
    table = (
        "<table><tr><th>Old 15 exposed diagnostics</th><th>Before earlier fixes</th>"
        "<th>Frozen current parser</th></tr>"
        + "".join(
            "<tr>" + "".join("<td>" + esc(str(x)) + "</td>" for x in row) + "</tr>" for row in rows
        )
        + "</table>"
    )
    decisions = [
        {
            "suggestion": "Keep strict code/title/description and boundaries as the main metric",
            "decision": "RETAINED_UNCHANGED",
            "evidence": "The exact scorer also requires complete description, physical pages and "
            "continuation. Every strict total was checked against the sealed release.",
        },
        {
            "suggestion": "Add per-field engineering scores and averages",
            "decision": "IMPLEMENTED_WITH_EXPLICIT_DENOMINATORS",
            "evidence": "Existing scores were conditional on aligned records. Phase 2 adds "
            "end-to-end exact resolved field delivery, per-course five-field scores, "
            "micro/macro averages and explicit missing/unresolved counts.",
        },
        {
            "suggestion": "Award eight equal points including separate start/end boundaries",
            "decision": "PARTIAL_ADOPTION_SOURCE_ANNOTATIONS_REQUIRED",
            "evidence": "Start/end anchor roles are not independently adjudicated by the imported "
            "answers. The eight-item panel marks them unassessed. The average covers "
            "five exact fields; confirmed owned extent and continuation stay separate.",
        },
        {
            "suggestion": "Use the screenshot's illustrative field totals as current measurements",
            "decision": "REPLACED_WITH_VERIFIED_COUNTS",
            "evidence": "The current exposed 15 score 12 code, 12 title, 11 resolved description, "
            "10 credits and 1 prerequisite. Twelve raw description values match, but "
            "one is partial/unresolved. Raw equality is shown separately in Phase 1.",
        },
        {
            "suggestion": "Field scoring will make development more efficient",
            "decision": "USEFUL_DIAGNOSTIC_EXPECTATION_NOT_MEASURED_EXTRACTION_GAIN",
            "evidence": "The scores expose missing records, metadata failures and required "
            "continuation misses. This phase changes diagnostics only; no extraction "
            "gain or reduction in development time is claimed.",
        },
    ]
    write_json(output / "suggestion_decisions.json", decisions)
    (output / "index.html").write_text(
        f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase 2 — field scores beside strict course completion</title><style>
body{{font:16px/1.55 system-ui;max-width:1150px;margin:30px auto;padding:0 20px;color:#182630}}
table{{border-collapse:collapse;width:100%}}
td,th{{padding:10px;border:1px solid #ccd5dd;text-align:left}}
th{{background:#edf3f8}}a{{color:#165eaa}}</style>
<h1>Phase 2: exact field scoring is implemented</h1>
<p>The suggestion is appropriate: keep strict complete-course extraction as the main result,
and use field scores to explain failures. Existing conditional field scores remain available.
The new end-to-end scores retain missed courses, failed pages and unresolved alignments as
zero delivery credit. Source-unresolved fields are excluded and counted explicitly.</p>
{table}
<p>Five-field micro recall pools exact code/title/description/credits/prerequisite opportunities.
Macro recall equally averages the five available field-type recalls. On this exposed set,
current exact fields earn {current["five_field_average"]["pooled_correct"]}/
{current["five_field_average"]["pooled_source_eligible"]} points. This describes field delivery;
it does not replace the joint strict score or include false-course precision.</p>
<p>The screenshots mix illustrative numbers with an older zero-of-fifteen core result.
Current core completion is ten-of-fifteen; current all-fields completion remains zero-of-fifteen.
An exact raw description value can still have partial/unresolved status. The resolved exact
description score is 11/15; raw description value equality is 12/15.</p>
<p>Continuation status agrees for 13/15 courses, but the one required cross-page course receives
no delivery credit. The required-continuation result is 0/1. A high score on ordinary NONE
statuses cannot establish successful continuation assembly.</p>
<p>Separate start/end anchor roles are not reliably distinguished by the imported source
answers. Those panel items stay NOT_ASSESSED; there is no invented seven-of-eight grade.
Confirmed owned extent remains a separately reported joint block/page/continuation comparison.</p>
<h2>Detailed reports and every course's field scores</h2><ul>
{"".join(f'<li><a href="{esc(name)}/index.html">{esc(name)}</a></li>' for name in releases)}
</ul><p>The original reports cover 52 reviewed courses including the old 15 exposed diagnostic
examples; the expanded reports cover 128 and include the original sample. Pending and
source-insufficient page reviews are not negative training examples. Reused source reviews
are not independent adjudication or evidence of held-out generalization.</p>
<p><a href="suggestion_decisions.json">Decisions on the suggestions</a> ·
<a href="phase2_release_manifest.json">Release provenance</a></p>
<p>The reserved cohort remains sealed. This phase reads only registered, checksum-pinned
exposed development evaluations and their already-imported answers. No reserved cohort
images, text, predictions, identities, selection records or review answers are inspected,
scored or summarized. All 18 phases must be complete and the parser frozen before access.</p>
</html>""",
        encoding="utf-8",
    )
    verify_files(WORKSPACE, freeze["file_sha256"])
    write_json(
        output / "phase2_release_manifest.json",
        {
            "schema_version": "course-parser-field-scoring-phase2-release-v1",
            "phase_number": 2,
            "status": "PHASE_IMPLEMENTED_STRICT_SCORE_UNCHANGED",
            "phase_execution_policy_sha256": sha256(POLICY_PATH),
            "frozen_parser_code_verified_before_and_after": True,
            "parser_freeze_manifest_sha256": sha256(freeze_path),
            "reserved_cohort_access": "SEALED",
            "reserved_cohort_material_read": False,
            "reserved_review_answers_used": False,
            "all_18_phases_claimed_complete": False,
            "strict_score_changed": False,
            "parser_changed": False,
            "answers_changed": False,
            "new_code_sha256": {
                str(p.relative_to(WORKSPACE)): sha256(p)
                for p in (
                    WORKSPACE / "src/course_parser/field_scoring.py",
                    WORKSPACE / "tests/test_field_scoring.py",
                    Path(__file__),
                )
            },
            "file_sha256": artifact_hashes(output),
        },
    )
    print(output / "index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=WORKSPACE / "artifacts/field-scoring-phase2-v1"
    )
    build(parser.parse_args().output)
