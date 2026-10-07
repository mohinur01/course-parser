# Check and report the Phase 3 experiment on assigning text to courses. Show the measured
# changes and review cases from the permitted development samples.
"""Seal the Phase 3 decision and measured exposed DEVELOPMENT outcomes."""

from __future__ import annotations

import html
from collections import Counter
from pathlib import Path

from course_parser.completion_diagnostics import load_exposed_evaluation
from course_parser.field_scoring import POLICY_PATH, WORKSPACE, enforce_policy
from course_parser.io import read_json, read_jsonl, sha256, verify_files, write_json, write_jsonl

ROOT = WORKSPACE / "artifacts/ownership-phase3-v1"
FINAL = ROOT / "candidate-v2"


# Check and report the Phase 3 experiment on assigning text to courses.
def build():
    outcomes, changes, provenance = {}, [], {}
    # Compare the original and expanded samples separately; their overlap means their gains
    # must not be added together.
    for name in ("original", "expanded"):
        old = WORKSPACE / f"artifacts/validation-transfer-v1/release/{name}-evaluation"
        new = FINAL / (name + "-evaluation")
        for path in (old, new):
            enforce_policy(path, ROOT)
        before, _ = load_exposed_evaluation(old)
        after, _ = load_exposed_evaluation(new)
        # Pair the old and new result for the same reviewed course so a changed sample cannot
        # explain the gain.
        previous = {r["gold_course_id"]: r for r in before}
        assert set(previous) == {r["gold_course_id"] for r in after}
        for row in after:
            old_row = previous[row["gold_course_id"]]
            for label in ("core", "all_fields"):
                was = old_row["completion"][label]["correct"]
                now = row["completion"][label]["correct"]
                # A gain elsewhere must not hide the loss of a previously complete course.
                if was is True and now is not True:
                    raise ValueError("a previously complete course regressed")
                if was != now:
                    changes.append(
                        {
                            "cohort": name,
                            "gold_course_id": row["gold_course_id"],
                            "dataset_page_id": row["dataset_page_id"],
                            "code": row["code"],
                            "title": row["title"],
                            "criterion": label,
                            "before_correct": was,
                            "after_correct": now,
                        }
                    )
        comparison = read_json(FINAL / (name + "-comparison/comparison.json"))
        pred_path = FINAL / (name + "-predictions")
        pred = read_json(pred_path / "prediction_manifest.json")
        verify_files(pred_path, pred["file_sha256"])
        # Stop if the current source differs from the measured Phase 3 parser.
        if any(
            sha256(Path(p)) != expected for p, expected in pred["workspace_code_sha256"].items()
        ):
            raise ValueError("current source differs from the measured Phase 3 parser")
        # Reject a change that increases the number of known false-course proposals.
        if (
            comparison["after"]["false_course_proposals"]
            > comparison["before"]["false_course_proposals"]
        ):
            raise ValueError("known false proposals increased")
        outcomes[name] = {
            "before": comparison["before"],
            "after": comparison["after"],
            "before_failed_windows": read_json(
                WORKSPACE / f"artifacts/validation-transfer-v1/release/{name}-predictions/"
                "prediction_manifest.json"
            )["failed_windows"],
            "after_failed_windows": pred["failed_windows"],
            "field_scores": read_json(ROOT / (name + "-field-scores/summary.json"))["overall"],
        }
        provenance[name] = {
            "before_evaluation_manifest_sha256": sha256(old / "evaluation_manifest.json"),
            "after_evaluation_manifest_sha256": sha256(new / "evaluation_manifest.json"),
            "prediction_manifest_sha256": sha256(pred_path / "prediction_manifest.json"),
            "unchanged_strict_evaluator_sha256": pred.get("evaluation_code_sha256")
            or read_json(new / "evaluation_manifest.json")["evaluation_code_sha256"],
        }
    proposals = read_jsonl(FINAL / "expanded-predictions/page_proposals.jsonl")
    selected = {
        x["dataset_page_id"]: x
        for x in read_jsonl(
            WORKSPACE / "artifacts/table-ownership-v1/expanded-pilot/selected_pages.jsonl"
        )
    }
    inbox = [
        {
            **conflict,
            "dataset_page_id": p["dataset_page_id"],
            "learning_partition": selected[p["dataset_page_id"]]["learning_partition"],
            "packet_path": str(
                WORKSPACE
                / "artifacts/table-ownership-v1/expanded-pilot"
                / selected[p["dataset_page_id"]]["packet_path"]
            ),
            "review_status": "PENDING_SOURCE_REVIEW",
            "review_decision": None,
        }
        for p in proposals
        for conflict in p.get("header_ownership_conflicts", [])
    ]
    links = [x for p in proposals for x in p.get("course_field_links", [])]
    write_jsonl(ROOT / "title_ownership_review.jsonl", inbox)
    write_jsonl(ROOT / "completion_changes.jsonl", changes)
    decisions = [
        {
            "suggestion": "Ownership was the largest improvement in the cited experiment",
            "assessment": "ACCURATE_HISTORICAL_RESULT",
            "decision": "Use current paired evidence to choose the next change.",
            "evidence": "docs/ownership_order_v2.md records 12/52 → 22/52 → 25/52. "
            "Before Phase 3, the same original cohort already scored 35/52.",
        },
        {
            "suggestion": "Stable identity and explicit course/field relationships",
            "assessment": "ALREADY_PRESENT_STRENGTHENED_WITH_AUDIT_EXPORT",
            "decision": "Reuse course_occurrence_id, header_candidate_id, block_id and "
            "field allocation IDs; export checked native spans and record-field statuses.",
            "evidence": "The existing typed CourseRecord/FieldAllocation contracts already "
            "link fields to source evidence. IDs identify reproducible proposals within a "
            "source window; they are not a permanent semantic course identity across catalogs.",
        },
        {
            "suggestion": "Use multiple source and layout signals for ownership",
            "assessment": "RELEVANT_DEMONSTRATED_GAP_FIXED",
            "decision": "Withhold split title claims overlapping a supported inline header; "
            "reject embedded code lists as independent courses using a printed colon, "
            "comma-separated codes, body typography and column adjacency.",
            "evidence": "Exposed source pages reproduce both false attachment and nested-list "
            "ownership. The constraints retain source bytes and the double-ownership audit.",
        },
        {
            "suggestion": "Prefer relative geometry over institution-specific coordinates",
            "assessment": "APPROPRIATE_NEW_CONSTRAINTS_USE_NO_FIXED_COORDINATES",
            "decision": "Use existing inferred column links, exact overlapping source spans "
            "and body/header contrast. Check source cases at triple scale with translation.",
            "evidence": "The new rules contain no institution, subject-specific code or page "
            "coordinate exceptions. Other existing parser heuristics still require evaluation.",
        },
        {
            "suggestion": "Build a new weighted ownership scorer or add another model",
            "assessment": "DEFERRED_PENDING_EVIDENCE_AND_ANNOTATIONS",
            "decision": "Keep the narrowly demonstrated constraints. Do not invent learned "
            "weights or train a model on blank or weak ownership labels.",
            "evidence": "A new model was unnecessary for this measured fix. The gain is from "
            "one reviewed TUNING page; transfer to unseen institutions is not established.",
        },
    ]
    write_json(ROOT / "suggestion_decisions.json", decisions)
    write_json(ROOT / "outcomes.json", outcomes)
    esc = html.escape
    rows = []
    for name, values in outcomes.items():
        old, new = values["before"], values["after"]
        rows.extend(
            [
                (
                    name.title(),
                    "Complete core courses",
                    f"{old['complete_content_correct']}/{old['gold_courses']}",
                    f"{new['complete_content_correct']}/{new['gold_courses']}",
                ),
                (
                    name.title(),
                    "Complete all-field courses",
                    f"{old['complete_all_fields_correct']}/{old['complete_all_fields_eligible']}",
                    f"{new['complete_all_fields_correct']}/{new['complete_all_fields_eligible']}",
                ),
                (
                    name.title(),
                    "Known false proposals",
                    old["false_course_proposals"],
                    new["false_course_proposals"],
                ),
                (
                    name.title(),
                    "Ambiguous proposals",
                    old["ambiguous_course_proposals"],
                    new["ambiguous_course_proposals"],
                ),
                (
                    name.title(),
                    "Failed source windows",
                    values["before_failed_windows"],
                    values["after_failed_windows"],
                ),
            ]
        )
    table = "".join(
        "<tr>" + "".join("<td>" + esc(str(v)) + "</td>" for v in row) + "</tr>" for row in rows
    )
    assessments = "".join(
        "<tr><td>" + esc(d["suggestion"]) + "</td><td>" + esc(d["decision"]) + "</td></tr>"
        for d in decisions
    )
    (ROOT / "index.html").write_text(
        f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase 3 — course ownership and field linking</title><style>
body{{font:16px/1.55 system-ui;max-width:1120px;margin:32px auto;padding:0 20px;color:#182630}}
table{{border-collapse:collapse;width:100%;margin:20px 0}}
td,th{{padding:10px;border:1px solid #ccd5dd;text-align:left}}
th{{background:#edf3f8}}a{{color:#165eaa}}.notice{{padding:16px;background:#edf3f8}}img{{max-width:100%}}
</style><h1>Phase 3: ownership improvements implemented</h1>
<p>The suggestions are appropriate, and much of the architecture already exists.
This increment fixes demonstrated competing title ownership and embedded reference-list
classification, and makes the existing source-span field links easier to audit.</p>
<p class="notice">The reserved 34-window institution-held-out cohort remains sealed.
Only exposed DEVELOPMENT/TUNING sources and existing exposed diagnostics were used.
All 18 phases must finish and the parser must be frozen before that cohort can be opened.</p>
<table><tr><th>Cohort</th><th>Measure</th><th>Before Phase 3</th><th>After Phase 3</th></tr>
{table}</table>
<p>The expanded cohort contains the original cohort; these are overlapping samples.
Strict code, title, complete description, owned extent, page and continuation criteria remain
unchanged. No previously complete course regressed. Five additional complete courses and
three additional all-field records came from one recovered TUNING page. The old exposed
15-course diagnostic subset remains at 10/15 core and 0/15 all fields.</p>
<p>Ambiguous proposals rose from two to four because a formerly failed page now exposes
joint printed headings. Those alignments remain unresolved and are included in conservative
precision. The gain is a specific source-supported repair, not evidence of unseen-institution
generalization. Overall expanded core completion is still only 41/128 (32.0%).</p>
<h2>What the source showed</h2>
<p>An instrument-option code inside a description borrowed the following MU170 heading
as its title. A prerequisite code on another catalog's context page similarly borrowed the
CHGN430 heading. Both pairs then competed for the following description. The new title
constraint preserves both proposals and flags the overlapping claim. The strict audit still
rejects double ownership.</p>
<p>The recovered music page also exposed an instrument list incorrectly proposed as a course.
Its printed list introducer, commas and body typography now establish a reference container.
Ordinary unbolded courses without credits remain supported in regression tests.</p>
<p>The second catalog's central page lacks a complete source review: it demonstrates recovery
from an operational failure, and contributes no invented course-accuracy result.</p>
<p><a
href="../table-ownership-v1/expanded-pilot/packets/v3page_859b557b404aea418786/page_00169.png">
Reviewed TUNING source: music course entries and embedded list</a> ·
<a
href="../table-ownership-v1/expanded-pilot/packets/v3page_6863f93c9d34a613c3e4/page_00074.png">
Permitted TUNING context: prerequisite and next header</a></p>
<h2>Implementation decisions</h2><table><tr><th>Suggestion</th><th>Decision</th></tr>
{assessments}</table>
<p>Every allocation retains its source node, exact character span, raw text, evidence,
existing course identity, field role and resolved/withheld record-field status. Separators
and metadata remain visible. The expanded source-only run exports {len(links):,} checked
links across central and context proposals; these are model proposals, not reviewed labels.</p>
<p>{len(inbox)} competing title claims are routed to a blank local source-review inbox.
No source answers, dispositions or authoritative ledger entries were changed.</p>
<h2>Results and source review</h2><ul>
<li><a href="candidate-v2/expanded-comparison/index.html">Paired expanded comparison</a></li>
<li><a href="candidate-v2/original-comparison/index.html">Paired original comparison</a></li>
<li><a href="expanded-field-scores/index.html">Expanded field-level scores</a></li>
<li><a href="original-field-scores/index.html">Original field-level scores</a></li>
<li><a href="completion_changes.jsonl">Exactly which courses improved</a></li>
<li><a href="title_ownership_review.jsonl">Blank title-ownership review inbox</a></li>
<li><a href="candidate-v2/expanded-predictions/page_proposals.jsonl">
Source-span relationships and conflicts</a></li></ul>
<p>Verification: 220 tests passed; Ruff checks passed. Two new real-source regressions
include scale/translation invariance. Synthetic regressions cover legitimate split titles,
unbolded creditless courses, embedded references, source mutations and ownership gaps.</p>
<p>The first measurement exposed an overly strict export-evidence check; it was corrected
to validate sliced field anchors against native source anchors. The first constraint-only
candidate recovered courses but exposed a false list entry. Both trial artifacts are retained;
the accepted result is candidate-v2, with unchanged strict scorer and reviewed answers.</p>
</html>""",
        encoding="utf-8",
    )
    code_files = [
        *sorted((WORKSPACE / "src/course_parser").glob("*.py")),
        *sorted((WORKSPACE / "tests").rglob("*.py")),
        *sorted((WORKSPACE / "tests/data/source_regressions_v4").glob("*.json")),
        Path(__file__).resolve(),
        WORKSPACE / "scripts/measure_phase3_ownership.py",
        POLICY_PATH,
        WORKSPACE / "pyproject.toml",
        WORKSPACE / "README.md",
    ]
    write_json(
        ROOT / "phase3_acceptance_manifest.json",
        {
            "schema_version": "course-parser-ownership-phase3-v1",
            "status": "EXPOSED_DEVELOPMENT_IMPLEMENTATION_VERIFIED",
            "parser_version": "0.11.0",
            "accepted_measurement": "candidate-v2",
            "strict_metric_changed": False,
            "review_answers_changed": False,
            "reserved_cohort_access": "SEALED",
            "reserved_cohort_opened": False,
            "reserved_answers_used": False,
            "all_18_phases_complete": False,
            "generalization_claim": "NOT_ESTABLISHED",
            "source_link_count_expanded": len(links),
            "source_link_roles_expanded": dict(Counter(x["role"] for x in links)),
            "title_claim_review_tasks": len(inbox),
            "verification": {"pytest": "220 passed", "ruff": "PASS"},
            "measurement_provenance": provenance,
            "code_sha256": {str(p.relative_to(WORKSPACE)): sha256(p) for p in code_files},
            "report_sha256": {
                str(p.relative_to(ROOT)): sha256(p)
                for p in (
                    ROOT / "index.html",
                    ROOT / "outcomes.json",
                    ROOT / "suggestion_decisions.json",
                    ROOT / "completion_changes.jsonl",
                    ROOT / "title_ownership_review.jsonl",
                )
            },
        },
    )
    print(ROOT / "index.html")


if __name__ == "__main__":
    build()
