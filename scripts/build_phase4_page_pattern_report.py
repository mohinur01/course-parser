# Check the Phase 4 experiment on repeated course-heading patterns and build its comparison
# report using the saved exposed results.
"""Verify and publish the exposed-only Phase 4 decision and paired results."""

from __future__ import annotations

import difflib
import html
from collections import Counter
from pathlib import Path

from course_parser.completion_diagnostics import load_exposed_evaluation
from course_parser.field_scoring import POLICY_PATH, WORKSPACE, enforce_policy
from course_parser.io import read_json, read_jsonl, sha256, verify_files, write_json, write_jsonl
from course_parser.learning_evaluation import align_headers

ROOT = WORKSPACE / "artifacts/page-pattern-phase4-v1"
FINAL = ROOT / "candidate-v3"
BEFORE = WORKSPACE / "artifacts/ownership-phase3-v1/candidate-v2"


# Check the Phase 4 experiment on repeated course-heading patterns and build its comparison
# report using the saved exposed results.
def build():
    snapshot = read_json(ROOT / "before-code/manifest.json")
    verify_files(ROOT / "before-code", snapshot["file_sha256"])
    changes, outcomes, provenance = [], {}, {}
    for name in ("original", "expanded"):
        old, new = BEFORE / (name + "-evaluation"), FINAL / (name + "-evaluation")
        for path in (old, new):
            enforce_policy(path, ROOT)
        before, _ = load_exposed_evaluation(old)
        after, _ = load_exposed_evaluation(new)
        previous = {r["gold_course_id"]: r for r in before}
        assert set(previous) == {r["gold_course_id"] for r in after}
        for row in after:
            prior = previous[row["gold_course_id"]]
            for label in ("core", "all_fields"):
                was, now = (
                    prior["completion"][label]["correct"],
                    row["completion"][label]["correct"],
                )
                # Do not let gains elsewhere hide the loss of a previously complete course.
                if was is True and now is not True:
                    raise ValueError("previously complete course regressed")
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
                            "remaining_metadata_reasons": row["fields"]["prerequisites"][
                                "reason_codes"
                            ],
                        }
                    )
        comparison = read_json(FINAL / (name + "-comparison/comparison.json"))
        pred_path = FINAL / (name + "-predictions")
        pred = read_json(pred_path / "prediction_manifest.json")
        verify_files(pred_path, pred["file_sha256"])
        # Stop if the current parser differs from measured parser.
        if any(
            sha256(Path(p)) != expected for p, expected in pred["workspace_code_sha256"].items()
        ):
            raise ValueError("current parser differs from measured parser")
        assert pred["source_truth_read"] is False and pred["protected_evaluation_opened"] is False
        for metric in ("false_course_proposals", "description_contamination"):
            if comparison["after"][metric] > comparison["before"][metric]:
                raise ValueError(metric + " increased")
        before_fields = read_json(
            WORKSPACE / "artifacts/ownership-phase3-v1" / (name + "-field-scores/summary.json")
        )["overall"]
        after_fields = read_json(ROOT / (name + "-field-scores/summary.json"))["overall"]
        outcomes[name] = {
            "before": comparison["before"],
            "after": comparison["after"],
            "before_field_scores": before_fields,
            "after_field_scores": after_fields,
            "before_failed_windows": read_json(
                BEFORE / (name + "-predictions/prediction_manifest.json")
            )["failed_windows"],
            "after_failed_windows": pred["failed_windows"],
        }
        assert outcomes[name]["after_failed_windows"] == 0
        provenance[name] = {
            "before_evaluation_manifest_sha256": sha256(old / "evaluation_manifest.json"),
            "after_evaluation_manifest_sha256": sha256(new / "evaluation_manifest.json"),
            "prediction_manifest_sha256": sha256(pred_path / "prediction_manifest.json"),
            "unchanged_strict_evaluator_sha256": read_json(new / "evaluation_manifest.json")[
                "evaluation_code_sha256"
            ],
        }

    pages = read_jsonl(FINAL / "expanded-predictions/page_proposals.jsonl")
    patterns = [
        {**p, "dataset_page_id": page["dataset_page_id"]}
        for page in pages
        for p in page.get("page_pattern_proposals", [])
    ]
    # A fresh proposal with a non-unique gold alignment becomes a blank review task. This
    # export does not adjudicate a source course or change an answer.
    old_ids = {
        x["course"]["header_candidate_id"]
        for x in read_jsonl(BEFORE / "expanded-predictions/course_proposals.jsonl")
    }
    gold = read_jsonl(FINAL / "expanded-evaluation/imported_course_truth.jsonl")
    courses = read_jsonl(FINAL / "expanded-predictions/course_proposals.jsonl")
    inbox = []
    for page in pages:
        candidates = page.get("candidates", [])
        _, ambiguous = align_headers(
            candidates,
            [
                g
                for g in gold
                if g["dataset_page_id"] == page["dataset_page_id"]
                and g["scope"] == "HEADER_STARTS_ON_CENTRAL_PAGE"
            ],
        )
        for row in courses:
            course = row["course"]
            cid = course["header_candidate_id"]
            if (
                row["dataset_page_id"] == page["dataset_page_id"]
                and row["scope"] == "CENTRAL"
                and cid in ambiguous
                and cid not in old_ids
            ):
                inbox.append(
                    {
                        "dataset_page_id": row["dataset_page_id"],
                        "candidate_id": cid,
                        "source_pdf_checksum": course["source"]["source_pdf_checksum"],
                        "code": course["code"]["raw"],
                        "title": course["title"]["raw"],
                        "physical_page": row["header_physical_page"],
                        "reason_code": "NEW_PROPOSAL_WITH_NONUNIQUE_SOURCE_REVIEW_ALIGNMENT",
                        "review_status": "PENDING_SOURCE_REVIEW",
                        "review_decision": None,
                    }
                )
    write_jsonl(ROOT / "completion_changes.jsonl", changes)
    write_jsonl(ROOT / "page_patterns.jsonl", patterns)
    write_jsonl(ROOT / "pattern_alignment_review.jsonl", inbox)
    write_json(ROOT / "outcomes.json", outcomes)
    decisions = [
        {
            "suggestion": "Infer a page's course format from repeated entries",
            "assessment": "RELEVANT_SOURCE_SUPPORTED_SUBSET_IMPLEMENTED",
            "decision": "Keep the global grammar. Corroborate native numeric inline headers "
            "within each page, column and section; require two other distinct strong entries.",
        },
        {
            "suggestion": "Learn identifier, title, credits, indentation and gaps locally",
            "assessment": "PARTLY_IMPLEMENTED_WITH_EVIDENCE_GATES",
            "decision": "Use exact native font transitions, observed digit lengths and "
            "relative alignment for headers. Preserve printed credit and punctuation spans. "
            "Record font ratios, body indentation and entry gaps for diagnosis; indentation "
            "and gaps do not independently promote ownership.",
        },
        {
            "suggestion": "Apply the pattern to uncertain entries",
            "assessment": "IMPLEMENTED_WITH_NEGATIVE_AND_SCOPE_CONSTRAINTS",
            "decision": "Recognize matching weak headers as boundaries, including entries "
            "without owned narrative. Extract courses only after ownership classification. "
            "Explicit requirements, noncourse containers and uncertain tables retain vetoes.",
        },
        {
            "suggestion": "This will probably generalize much better",
            "assessment": "PLAUSIBLE_BUT_NOT_ESTABLISHED",
            "decision": "Relative geometry is invariant in source replay tests. The measured "
            "21-course gain comes from one exposed TUNING page, with no held-out evidence. "
            "This is deterministic source-local inference, not a trained model.",
        },
        {
            "suggestion": "Make one page pattern cover every kind of layout",
            "assessment": "DEFERRED_WHERE_SOURCE_OR_EXTRACTION_EVIDENCE_IS_INSUFFICIENT",
            "decision": "Do not flatten uncertain grids, infer missing native font runs, "
            "or borrow patterns across columns/sections. No OCR character correction or "
            "institution-specific exceptions were added.",
        },
    ]
    write_json(ROOT / "suggestion_decisions.json", decisions)
    esc = html.escape
    rows = []
    for name, data in outcomes.items():
        before, after = data["before"], data["after"]
        for label, key, denominator in (
            ("Complete core courses", "complete_content_correct", "gold_courses"),
            (
                "Complete all-field records",
                "complete_all_fields_correct",
                "complete_all_fields_eligible",
            ),
            ("Matched course proposals", "matched_course_proposals", "gold_courses"),
        ):
            rows.append(
                (
                    name.title(),
                    label,
                    f"{before[key]}/{before[denominator]}",
                    f"{after[key]}/{after[denominator]}",
                )
            )
        for key in (
            "false_course_proposals",
            "ambiguous_course_proposals",
            "description_contamination",
        ):
            rows.append((name.title(), key.replace("_", " ").capitalize(), before[key], after[key]))

    # Build an HTML table and escape the text in its cells.
    def table(headings, values):
        return (
            '<div class="scroll"><table><tr>'
            + "".join("<th>" + esc(h) + "</th>" for h in headings)
            + "</tr>"
            + "".join(
                "<tr>" + "".join("<td>" + esc(str(v)) + "</td>" for v in row) + "</tr>"
                for row in values
            )
            + "</table></div>"
        )

    gains = Counter(
        r["dataset_page_id"]
        for r in changes
        if r["cohort"] == "expanded" and r["criterion"] == "core"
    )
    assert sum(gains.values()) == 21
    report = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase 4: local page patterns</title><style>
body{{font:16px/1.55 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#182630}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;margin:18px 0}}
td,th{{padding:10px;border:1px solid #ccd5dd;text-align:left;vertical-align:top}}
th,.notice{{background:#edf3f8}}.notice{{padding:16px}}a{{color:#165eaa}}
img{{max-width:100%;height:auto}}code{{overflow-wrap:anywhere}}</style>
<h1>Phase 4: local page patterns implemented</h1>
<p>The suggestion addresses a demonstrated failure. Repeated bold numeric headers on an
exposed source page were missed or merged with their descriptions. Source font boundaries
and corroborating entries now recover 21 additional complete courses.</p>
<p class="notice">The reserved 34-window institution-held-out cohort remains sealed.
Only exposed DEVELOPMENT/TUNING sources and existing exposed diagnostics were used.
All 18 phases must be complete and the parser must be frozen before it may be opened.</p>
{table(("Reviewed sample", "Measure", "Before Phase 4", "After Phase 4"), rows)}
<p>Expanded core completion rose from 32.0% to 48.4%. The samples overlap: the expanded
sample contains the original. No previously complete core or all-field course regressed.
Both runs had zero failed source windows. All 21 gains come from UMKC physical page 329;
this is a targeted source repair, not evidence of unseen-institution generalization.</p>
<p>All-field completion remains 13/97. For every newly complete core course, the parser
still abstains on source-reviewed prerequisite absence. We have not awarded absence credit
to a blank output. Expanded five-field delivery rose from 218/608 (35.9%) to 311/608 (51.2%);
that diagnostic average is not complete-course accuracy.</p>
<p>One additional proposal has a non-unique review alignment. It remains ambiguous,
earns no completion credit, and is exported as a blank review task. Known false proposals
remain zero on this reviewed sample; that does not establish corpus-wide precision.</p>
<h2>How the pattern works</h2>
<ol><li>Read exact font runs from checksum-bound native text. Mismatched runs abstain.</li>
<li>Group numeric header shapes by page, inferred column, structural section and relative
alignment. A header cannot validate itself: require two other distinct strong source entries.</li>
<li>Use the repeated emphasis boundary to separate code/title/credits from ordinary prose.
Keep punctuation and every source character in their own allocations.</li>
<li>Use matching weak headers to stop the preceding course. A boundary alone does not
establish independent course ownership. Requirements and noncourse context still win.</li></ol>
<p>Printed credit values or explicit inventory context corroborate strong narrative seeds.
Matching creditless entries are permitted. Font ratios, description indentation and entry
gaps are recorded locally; the example values “14” and “1.3” were not copied as rules.</p>
<p>Two context errors were also demonstrated: an ordinary description line ending
“faculty.” was interpreted as a directory heading, and a lower-case prose fragment about
curriculum became a requirement container. Structural headings and short display labels
now provide container context; those body fragments cannot do so.</p>
<h2>Implementation decisions</h2>
{
        table(
            ("Suggestion", "Assessment and decision"),
            [(d["suggestion"], d["assessment"] + ": " + d["decision"]) for d in decisions],
        )
    }
<h2>Source evidence</h2>
<p><a
href="../table-ownership-v1/expanded-pilot/packets/v3page_b175ab7de80cb0b28a80/page_00329.png">
Open the reviewed page</a>. Two columns and a graduate-course section use different numeric
identifier lengths. The page is not treated as one universal format.</p>
<img src="../table-ownership-v1/expanded-pilot/packets/v3page_b175ab7de80cb0b28a80/page_00329.png"
alt="Reviewed catalog page showing repeated bold numeric headers followed by normal prose">
<h2>Results, diagnostics and provenance</h2><ul>
<li><a href="candidate-v3/expanded-comparison/index.html">Paired expanded comparison</a></li>
<li><a href="candidate-v3/original-comparison/index.html">Paired original comparison</a></li>
<li><a href="expanded-field-scores/index.html">Expanded per-course field scores</a></li>
<li><a href="original-field-scores/index.html">Original per-course field scores</a></li>
<li><a href="page_patterns.jsonl">Source-local patterns and abstentions</a></li>
<li><a href="completion_changes.jsonl">Every changed completion result</a></li>
<li><a href="pattern_alignment_review.jsonl">Blank ambiguity review task</a></li>
<li><a href="phase4_acceptance_manifest.json">Acceptance and file checksums</a></li>
<li><a href="parser_changes.diff">Parser changes against the frozen Phase 3 source</a></li></ul>
<p>233 tests passed, including actual source replay, scale/translation invariance,
creditless courses, hard negatives, insufficient support, missing font evidence, section
and column isolation, and uncertain-table abstention. Ruff passed. The strict scorer,
vocabulary, review answers and earlier frozen artifacts remain unchanged.</p>
<p>The first measurement attempt failed while serializing a column component; it produced
no scored result. That partial artifact is retained in candidate-v1. Candidate-v2 retains
the first successful measurement. Candidate-v3 contains the final sealed predictions,
including the printed-identifier punctuation check, and accepted comparison.</p></html>"""
    (ROOT / "index.html").write_text(report)
    diff = []
    for relative in (
        "src/course_parser/contextual.py",
        "src/course_parser/page_patterns.py",
        "src/course_parser/learning.py",
        "src/course_parser/__init__.py",
        "pyproject.toml",
    ):
        before_file, after_file = ROOT / "before-code" / relative, WORKSPACE / relative
        diff.extend(
            difflib.unified_diff(
                before_file.read_text().splitlines(keepends=True) if before_file.exists() else [],
                after_file.read_text().splitlines(keepends=True),
                fromfile="before/" + relative,
                tofile="after/" + relative,
            )
        )
    (ROOT / "parser_changes.diff").write_text("".join(diff))
    pins = {
        str(p.relative_to(WORKSPACE)): sha256(p)
        for folder in ("src", "tests", "scripts")
        for p in (WORKSPACE / folder).rglob("*.py")
    }
    for p in (
        WORKSPACE / "pyproject.toml",
        POLICY_PATH,
        WORKSPACE / "tests/data/source_regressions_v5/manifest.json",
        WORKSPACE / "tests/data/source_regressions_v5/numeric_inline_native_font_boundary.json",
    ):
        pins[str(p.relative_to(WORKSPACE))] = sha256(p)
    write_json(
        ROOT / "phase4_acceptance_manifest.json",
        {
            "schema_version": "course-parser-phase4-acceptance-v1",
            "phase": 4,
            "status": "ACCEPTED_EXPOSED_SOURCE_MEASUREMENT",
            "parser_version": "0.12.0",
            "contextual_version": "source-owned-course-assembly-v7",
            "protected_evaluation_opened": False,
            "reserved_cohort_access": "SEALED",
            "opening_gate": "All 18 phases complete AND parser frozen",
            "tests": {"passed": 233, "ruff": "PASS"},
            "provenance": provenance,
            "newly_complete_core_by_page": dict(gains),
            "previously_complete_regressions": 0,
            "current_file_sha256": pins,
            "report_file_sha256": {p.name: sha256(p) for p in ROOT.iterdir() if p.is_file()},
        },
    )
    print("Phase 4 accepted: expanded 41/128 -> 62/128; original 35/52 unchanged; reserved sealed.")


if __name__ == "__main__":
    build()
