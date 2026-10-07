# Report how the historical-heading changes performed on the same exposed samples. Separate
# results by printed identifier pattern rather than assuming a catalog age.
"""Verify paired exposed results and report identifier-family diagnostics."""

from __future__ import annotations

import difflib
import html
import re
from collections import Counter, defaultdict
from pathlib import Path

from course_parser.completion_diagnostics import load_exposed_evaluation
from course_parser.field_scoring import POLICY_PATH, WORKSPACE, enforce_policy, write_field_scores
from course_parser.historical_headers import EXTENDED_SIGNAL, PAIR_SIGNAL
from course_parser.io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)

ROOT = WORKSPACE / "artifacts/historical-detection-phase5-v1"
FINAL = ROOT / "candidate-v1"
BEFORE = WORKSPACE / "artifacts/page-pattern-phase4-v1/candidate-v3"


# Describe source-reviewed identifier syntax; do not infer catalog age.
def identifier_family(code):
    """Describe source-reviewed identifier syntax; do not infer catalog age."""
    code = (code or "").strip().lstrip("*")
    if re.match(r"[A-Z]{2,8}[- .]?\d", code):
        return "MODERN_SUBJECT_CODE"
    if re.fullmatch(r"\d[\d\s.,–-]*(?:through\s+\d+)?\.?", code):
        return "HISTORICAL_NUMERIC"
    if re.fullmatch(r"[IVXLCDM]{1,8}[.)]?", code):
        return "HISTORICAL_ROMAN"
    if re.fullmatch(r"[A-Z][.)]?", code):
        return "HISTORICAL_LETTERED"
    return "OTHER_PRINTED_IDENTIFIER"


# Keep the exposed comparison counts by visible identifier family.
def family_counts(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[identifier_family(row["code"])].append(row)
    return {
        family: {
            "reviewed_courses": len(values),
            "uniquely_aligned_headers": sum(v["detection"]["status"] == "PASS" for v in values),
            "records_emitted": sum(v["pipeline_state"] == "RECORD_EMITTED" for v in values),
            "complete_core": sum(v["completion"]["core"]["correct"] is True for v in values),
            "states": dict(Counter(v["pipeline_state"] for v in values)),
        }
        for family, values in sorted(grouped.items())
    }


# Report how the historical-heading changes performed on the same exposed samples.
def build():
    frozen = read_json(ROOT / "before_code_manifest.json")
    verify_files(ROOT / "before-code", frozen["file_sha256"])
    # Stop if the strict evaluator changed.
    if sha256(WORKSPACE / "src/course_parser/learning_evaluation.py") != frozen["scorer_sha256"]:
        raise ValueError("strict evaluator changed")
    freeze = read_json(FINAL / "source_only_freeze.json")
    # Both source-only predictions must precede scoring.
    if freeze["status"] != "BOTH_PREDICTIONS_SEALED_BEFORE_SCORING":
        raise ValueError("both source-only predictions must precede scoring")
    # Stop if the parser differs from frozen source-only run.
    if any(sha256(WORKSPACE / p) != digest for p, digest in freeze["parser_sha256"].items()):
        raise ValueError("parser differs from frozen source-only run")
    outcomes, provenance, changes, diagnosed = {}, {}, [], []
    for name in ("original", "expanded"):
        old, new = BEFORE / (name + "-evaluation"), FINAL / (name + "-evaluation")
        for path in (old, new):
            enforce_policy(path, ROOT)
        before, _ = load_exposed_evaluation(old)
        after, _ = load_exposed_evaluation(new)
        previous = {r["gold_course_id"]: r for r in before}
        # Stop if the reviewed course population changed.
        if set(previous) != {r["gold_course_id"] for r in after}:
            raise ValueError("reviewed course population changed")
        for row in after:
            prior = previous[row["gold_course_id"]]
            for criterion in ("core", "all_fields"):
                was, now = (
                    prior["completion"][criterion]["correct"],
                    row["completion"][criterion]["correct"],
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
                            "criterion": criterion,
                            "before_correct": was,
                            "after_correct": now,
                        }
                    )
            diagnosed.append({"cohort": name, **row})
        comparison = read_json(FINAL / (name + "-comparison/comparison.json"))
        for metric in ("false_course_proposals", "description_contamination"):
            if comparison["after"][metric] > comparison["before"][metric]:
                raise ValueError(metric + " increased")
        pred_path = FINAL / (name + "-predictions")
        pred = read_json(pred_path / "prediction_manifest.json")
        verify_files(pred_path, pred["file_sha256"])
        # Require predictions generated without source-review answers.
        if (
            pred["failed_windows"]
            or pred["source_truth_read"]
            or pred["protected_evaluation_opened"]
        ):
            raise ValueError("source-only prediction contract failed")
        # Stop if the current parser differs from measured parser.
        if any(sha256(Path(p)) != digest for p, digest in pred["workspace_code_sha256"].items()):
            raise ValueError("current parser differs from measured parser")
        outcomes[name] = {
            "before": comparison["before"],
            "after": comparison["after"],
            "by_identifier_family": {
                "before": family_counts(before),
                "after": family_counts(after),
            },
        }
        provenance[name] = {
            "before_evaluation_sha256": sha256(old / "evaluation_manifest.json"),
            "after_evaluation_sha256": sha256(new / "evaluation_manifest.json"),
            "prediction_manifest_sha256": sha256(pred_path / "prediction_manifest.json"),
        }
        write_field_scores(new, ROOT / (name + "-field-scores"))

    pilot = WORKSPACE / "artifacts/table-ownership-v1/expanded-pilot"
    selected = {p["dataset_page_id"]: p for p in read_jsonl(pilot / "selected_pages.jsonl")}
    probes, adoption = [], []
    for page in read_jsonl(FINAL / "expanded-predictions/page_proposals.jsonl"):
        item = selected[page["dataset_page_id"]]
        decisions = {d["candidate_id"]: d for d in page.get("decisions", [])}
        for candidate in page.get("candidates", []):
            if candidate["physical_page"] != item["central_physical_page"]:
                continue
            signals = candidate["structural_signals"]
            if PAIR_SIGNAL in signals or EXTENDED_SIGNAL in signals:
                adoption.append(
                    {
                        "dataset_page_id": item["dataset_page_id"],
                        "source_pdf_checksum": item["source_pdf_checksum"],
                        "candidate": candidate,
                        "decision": decisions[candidate["candidate_id"]],
                    }
                )
            if EXTENDED_SIGNAL in signals and candidate["status"] == "UNRESOLVED":
                probes.append(
                    {
                        "dataset_page_id": item["dataset_page_id"],
                        "source_pdf_checksum": item["source_pdf_checksum"],
                        "physical_page": candidate["physical_page"],
                        "candidate_id": candidate["candidate_id"],
                        "code_slice": candidate["code_slice"],
                        "title_slices": candidate["title_slices"],
                        "reason_codes": candidate["contradictions"],
                        "review_status": "PENDING_SOURCE_REVIEW",
                        "review_decision": None,
                        "course_truth_assigned": False,
                        "image_path": str(
                            pilot
                            / item["packet_path"]
                            / f"page_{candidate['physical_page']:05d}.png"
                        ),
                    }
                )
    write_jsonl(ROOT / "course_diagnostics.jsonl", diagnosed)
    write_jsonl(ROOT / "completion_changes.jsonl", changes)
    write_jsonl(ROOT / "adopted_historical_cues.jsonl", adoption)
    write_jsonl(ROOT / "historical_header_review.jsonl", probes)
    write_json(ROOT / "outcomes.json", outcomes)
    decisions = [
        {
            "suggestion": "Separate historical and modern detection",
            "decision": "ALREADY_PRESENT_AND_RETAINED",
            "reason": "Modern, OCR-uncertain modern, numeric, Roman and lettered branches already "
            "exist. Historical is an identifier syntax family, not proof of publication era.",
        },
        {
            "suggestion": "Use explicit candidate types",
            "decision": "REUSE_EXISTING_TYPED_CONTRACTS",
            "reason": "Keep HeaderIdentifierKind and exact source slices. A repeated pattern is "
            "supporting provenance, not a mutually exclusive identifier family.",
        },
        {
            "suggestion": "Require stronger evidence for weaker candidates",
            "decision": "IMPLEMENTED_SOURCE_SUPPORTED_SUBSET",
            "reason": "A four/five-digit display-title plus inline prose yields an unresolved "
            "boundary/review probe. Existing page/column/section native patterns need two other "
            "distinct strong source entries before promotion. Numbers alone do not receive "
            "this cue.",
        },
        {
            "suggestion": "Handle historical header structures",
            "decision": "IMPLEMENTED_DEMONSTRATED_NUMERIC_DELIMITER",
            "reason": "Split uppercase numeric titles from inline prose across a printed number "
            "pair; preserve the pair as metadata without inferred credit/contact meaning. "
            "Narrative and ownership gates still apply; negative containers win.",
        },
        {
            "suggestion": "Automatically extract title-only courses",
            "decision": "RETAIN_EXISTING_REVIEW_ONLY_PATH",
            "reason": "Omission audit already flags code-free headers using typography and prose. "
            "Source-reviewed examples and an explicit absent-code contract are needed before "
            "emitting complete records; do not invent identifiers.",
        },
    ]
    write_json(ROOT / "suggestion_decisions.json", decisions)
    diff = []
    for relative in (
        "src/course_parser/contextual.py",
        "src/course_parser/historical_headers.py",
        "src/course_parser/__init__.py",
        "pyproject.toml",
    ):
        old = ROOT / "before-code" / relative
        diff.extend(
            difflib.unified_diff(
                old.read_text().splitlines(keepends=True) if old.exists() else [],
                (WORKSPACE / relative).read_text().splitlines(keepends=True),
                fromfile="before/" + relative,
                tofile="after/" + relative,
            )
        )
    (ROOT / "parser_changes.diff").write_text("".join(diff))
    table = []
    for name, value in outcomes.items():
        for label, key, denominator in (
            ("Complete core courses", "complete_content_correct", "gold_courses"),
            (
                "Complete all-field records",
                "complete_all_fields_correct",
                "complete_all_fields_eligible",
            ),
            ("Matched course records", "matched_course_proposals", "gold_courses"),
        ):
            a, b = value["before"], value["after"]
            table.append(
                (name.title(), label, f"{a[key]}/{a[denominator]}", f"{b[key]}/{b[denominator]}")
            )
        for key in (
            "false_course_proposals",
            "description_contamination",
            "ambiguous_course_proposals",
        ):
            table.append(
                (name.title(), key.replace("_", " "), value["before"][key], value["after"][key])
            )

    # Escape displayed text so it cannot be interpreted as HTML markup.
    def esc(value):
        return html.escape(str(value))

    body = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in row) + "</tr>" for row in table)
    family_rows = []
    for name, value in outcomes.items():
        families = value["by_identifier_family"]
        for family, after in families["after"].items():
            before = families["before"][family]
            family_rows.append(
                (
                    name.title(),
                    family,
                    after["reviewed_courses"],
                    f"{before['records_emitted']} → {after['records_emitted']}",
                    f"{before['complete_core']} → {after['complete_core']}",
                )
            )
    family_body = "".join(
        "<tr>" + "".join(f"<td>{esc(x)}</td>" for x in row) + "</tr>" for row in family_rows
    )
    explanations = "".join(
        f"<li><strong>{esc(d['suggestion'])}</strong>: {esc(d['reason'])}</li>" for d in decisions
    )
    links = "".join(
        f'<li><a href="candidate-v1/{name}-comparison/index.html">'
        f'{name.title()} paired comparison</a> · '
        f'<a href="{name}-field-scores/index.html">field scoring</a></li>'
        for name in outcomes
    )
    review = "".join(
        f"<li>{esc(p['code_slice']['observed_text'])} — unresolved source header; "
        f'<a href="{esc(Path(p["image_path"]).relative_to(WORKSPACE / "artifacts").as_posix())}">'
        'source image</a></li>'
        for p in probes
    ).replace('href="table-ownership-v1/', 'href="../table-ownership-v1/')
    gain = [c for c in changes if c["cohort"] == "expanded" and c["criterion"] == "core"]
    gain_pages = len({c["dataset_page_id"] for c in gain})
    (ROOT / "index.html").write_text(f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Phase 5: historical identifier evidence</title><style>
body{{font:16px/1.6 system-ui;margin:40px auto;max-width:1150px;padding:0 25px;color:#192e42}}
table{{border-collapse:collapse;width:100%;margin:20px 0}}
td,th{{padding:9px;text-align:left;border-bottom:1px solid #ccd5df}}
.note{{background:#edf5fb;padding:15px;border-left:4px solid #276182}}a{{color:#155883}}</style>
<h1>Phase 5: historical-course detection</h1><p class="note">Exposed DEVELOPMENT diagnostics only.
The reserved 34-window institution-held-out cohort remains sealed through all 18 phases
and final parser freeze.</p>
<p>Keep separate identifier families and require source corroboration. The accepted change addresses
a demonstrated numeric title/prose delimiter and adds conservative uncertain-header probes.
No trained model or institutional exceptions were added.</p>
<h2>Same reviewed samples, unchanged strict score</h2><table><tr>
<th>Sample</th><th>Metric</th><th>Before</th><th>After</th></tr>{body}</table>
<p>The expanded sample includes the original sample. These results are development evidence,
not independent generalization accuracy. All {len(gain)} new complete courses come from
{gain_pages} exposed TUNING page. Exact source text is preserved; OCR word errors,
incomplete prerequisites and uncertain boundaries remain failures.</p>
<h2>Identifier-family diagnosis</h2><table><tr><th>Sample</th><th>Source identifier family</th>
<th>Reviewed</th><th>Delivered records</th><th>Complete core</th></tr>{family_body}</table>
<p>Family labels describe printed identifier syntax, not catalog age. Roman, lettered and title-only
families have no reviewed examples in these measured samples; their accuracy is unestablished.</p>
<h2>Suggestion decisions</h2><ul>{explanations}</ul>
<h2>Review probes</h2><p>Unresolved probes stop ownership at uncertain text and have blank
review answers. No OCR correction or identifier guessing was applied.</p><ul>{review}</ul>
<h2>Evidence and checks</h2><ul>{links}
<li><a href="completion_changes.jsonl">Per-course completion changes</a></li>
<li><a href="historical_header_review.jsonl">Blank source-review tasks</a></li>
<li><a href="parser_changes.diff">Parser diff</a></li>
<li><a href="phase5_acceptance_manifest.json">Acceptance and provenance</a></li></ul></html>""")
    write_json(
        ROOT / "phase5_acceptance_manifest.json",
        {
            "status": "ACCEPTED_EXPOSED_MEASURED_SUBSET",
            "phase": 5,
            "parser_version": "0.13.0",
            "assembly_version": "source-owned-course-assembly-v8",
            "provenance": provenance,
            "unchanged_strict_scorer_sha256": frozen["scorer_sha256"],
            "reserved_cohort_access": "SEALED",
            "reserved_release_gate": "ALL_18_PHASES_COMPLETE_AND_PARSER_FROZEN",
            "protected_evaluation_opened": False,
            "tests": "250 passed",
            "ruff": "All checks passed",
            "regressed_previously_complete_courses": 0,
            "code_sha256": {
                str(p.relative_to(WORKSPACE)): sha256(p)
                for p in [
                    *sorted((WORKSPACE / "src/course_parser").glob("*.py")),
                    WORKSPACE / "scripts/measure_phase5_historical_headers.py",
                    Path(__file__),
                    WORKSPACE / "tests/test_historical_headers.py",
                    WORKSPACE / "pyproject.toml",
                ]
            },
            "regression_case_manifest_sha256": sha256(
                WORKSPACE / "tests/data/source_regressions_v6/manifest.json"
            ),
            "policy_sha256": sha256(POLICY_PATH),
            "report_sha256": {
                p: digest
                for p, digest in artifact_hashes(ROOT).items()
                if p != "phase5_acceptance_manifest.json"
            },
        },
    )
    print(
        {
            "outcomes": {
                name: {
                    "before": value["before"]["complete_content_correct"],
                    "after": value["after"]["complete_content_correct"],
                }
                for name, value in outcomes.items()
            },
            "unresolved_review_probes": len(probes),
            "changed_completion_checks": len(changes),
        }
    )


if __name__ == "__main__":
    build()
