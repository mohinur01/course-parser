# Compare results across institution families and test an experimental ranker by leaving one
# tuning family out at a time. Keep these development diagnostics separate from the reserved
# evaluation.
"""Exposed family diagnostics and fresh TUNING-only entry-ranker folds.

Cached complete-course scores are never presented as leave-one-out retraining.
No source pages or course-review answers are opened by this experiment.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
from collections import Counter
from pathlib import Path

from .entry_learning import classification_metrics, fit_ranker, probability
from .evaluation_design import (
    _metadata_file,
    audit_split_metadata,
    evaluation_role,
    load_exposed_split_metadata,
)
from .io import artifact_hashes, read_json, read_jsonl, sha256, write_json, write_jsonl

VERSION = "course-parser-institution-out-v1"
PARAMETERS = {"iterations": 600, "learning_rate": 0.3, "regularization": 0.02}
THRESHOLD = 0.5
ELIGIBLE = {"SCORED_DEVELOPMENT", "PARSER_STAGE_FAILED"}
FEATURES = {
    "bias",
    "header_supported",
    "title_present",
    "title_word_count",
    "detail_count",
    "context_confirmed",
    "contradiction_count",
}
LIMITS = [
    "Fresh fold weights exclude the held-out family, but rules, features and inventories "
    "were developed using exposed institutions. This is not untouched generalization.",
    "Ranker metrics are conditional on reviewed generated candidates. They do not measure "
    "unproposed headers, complete courses, ownership, boundaries or fields.",
    "Cached parser scores are a family breakdown, not parser retraining or leave-one-out tuning.",
    "No independent adjudication is assumed. Unreviewed windows contribute no invented labels.",
    "Small, selected, overlapping windows are not independent observations. Low performance "
    "does not establish a layout cause; OCR, source coverage and composition may matter.",
    "No catalog-template similarity or reserved-cohort isolation was inspected.",
    "Hyperparameters and threshold are fixed in advance. Subsequent changes informed by "
    "these folds remain development work and require a fresh evaluation for transfer claims.",
]


# Create a stable checksum of structured data for later identity checks.
def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# Average only defined results and report how many groups contributed.
def mean_defined(values: list) -> dict:
    eligible = [x for x in values if x is not None]
    return {
        "value": sum(eligible) / len(eligible) if eligible else None,
        "defined_families": len(eligible),
        "total_families": len(values),
    }


# Check every cached record against verified exposed source identities.
def bind_records(metadata: list[dict], pages: list[dict], examples: list[dict]) -> dict:
    """Check every cached record against checksum-bound exposed source identities."""
    audit = audit_split_metadata(metadata)
    by_id = {x["dataset_page_id"]: x for x in metadata}
    # Stop if the cached page inventory differs from bound metadata.
    if len(pages) != len(by_id) or {x["dataset_page_id"] for x in pages} != set(by_id):
        raise ValueError("cached page inventory differs from bound metadata")
    # Reject duplicate cached page.
    if len({x["dataset_page_id"] for x in pages}) != len(pages):
        raise ValueError("duplicate cached page")
    scored_ids = {x["dataset_page_id"] for x in pages if x["status"] == "SCORED_DEVELOPMENT"}
    seen = set()
    for row in pages + examples:
        page = by_id.get(row["dataset_page_id"])
        # Each cached record must belong to a page in the approved exposed metadata.
        if page is None:
            raise ValueError("cached record outside exposed metadata")
        for key in ("learning_partition", "learning_group"):
            # Stop if the cached record group/partition differs from metadata.
            if row[key] != page[key]:
                raise ValueError("cached record group/partition differs from metadata")
        role = evaluation_role(row["learning_partition"])
        # The cached record's role must agree with its original partition.
        if row.get("evaluation_role", role) != role:
            raise ValueError("cached record evaluation role contradicts partition")
        # Stop if the cached physical page differs from metadata.
        if row["physical_page"] != page["central_physical_page"]:
            raise ValueError("cached physical page differs from metadata")
    for row in examples:
        key = (row["dataset_page_id"], row["candidate_id"])
        # Reject duplicate or empty candidate identity.
        if not row["candidate_id"] or key in seen:
            raise ValueError("duplicate or empty candidate identity")
        seen.add(key)
        page = by_id[row["dataset_page_id"]]
        # Use candidate labels only from page selections with completed source reviews.
        if row["dataset_page_id"] not in scored_ids:
            raise ValueError("candidate labels require a scored source-reviewed window")
        # Stop if the candidate source identity differs from metadata.
        if row["source_pdf_checksum"] != page["source_pdf_checksum"]:
            raise ValueError("candidate source identity differs from metadata")
        # Candidate labels must come from a reviewed heading or a complete reviewed course
        # inventory.
        if (
            type(row["label"]) is not int
            or row["label"] not in (0, 1)
            or row["label_basis"]
            not in {
                "SOURCE_REVIEWED_HEADER_ALIGNMENT",
                "COMPLETE_SOURCE_INVENTORY_NO_HEADER_AT_CANDIDATE",
            }
        ):
            raise ValueError("candidate label lacks source-inventory provenance")
        # Reject missing candidate review source and version history.
        if not row.get("reviewer_id") or type(row.get("independently_adjudicated")) is not bool:
            raise ValueError("missing candidate review provenance")
        # Require structural features before fitting or evaluating this example.
        if not isinstance(row["features"], dict) or not row["features"]:
            raise ValueError("empty structural features")
        for key, value in row["features"].items():
            # Do not let keyword or institution shortcuts enter the structural feature set.
            if key not in FEATURES and not key.startswith(
                ("identifier:", "arrangement:", "context:", "signal:")
            ):
                raise ValueError("nonstructural candidate feature")
            # Reject invalid numeric candidate feature.
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("invalid numeric candidate feature")
    return audit


# Sum cached integer counts and require exact reconciliation with frozen totals.
def parser_family_diagnostics(pages: list[dict], membership: dict, frozen: dict) -> dict:
    """Sum cached integer counts and require exact reconciliation with frozen totals."""
    count_keys = [k for k, v in frozen.items() if type(v) is int]

    # Combine comparable counts and compute only ratios with defined denominators.
    def aggregate(rows):
        scored = [x for x in rows if x["status"] in ELIGIBLE]
        counts = {key: sum(x.get(key, 0) for x in scored) for key in count_keys}
        counts["pages"] = len(scored)
        counts["parser_failed_pages"] = sum(x["status"] == "PARSER_STAGE_FAILED" for x in scored)
        # Reject invalid cached count.
        if any(type(v) is not int or v < 0 for v in counts.values()):
            raise ValueError("invalid cached count")
        rates = {}
        for name in ("complete_content", "complete_all_fields"):
            correct, eligible = counts[name + "_correct"], counts[name + "_eligible"]
            # Correct completions cannot exceed the number of eligible source courses.
            if correct > eligible:
                raise ValueError("cached completion exceeds denominator")
            rates[name + "_recall"] = correct / eligible if eligible else None
        return {
            "counts": counts,
            "rates": rates,
            "window_status_counts": dict(sorted(Counter(x["status"] for x in rows).items())),
            "windows": len(rows),
            "excluded_windows": len(rows) - len(scored),
            "evaluation_roles": sorted({evaluation_role(x["learning_partition"]) for x in rows}),
        }

    pooled = aggregate(pages)
    # Stop if the cached family counts do not reconcile with frozen strict evaluation.
    if pooled["counts"] != {k: frozen[k] for k in count_keys}:
        raise ValueError("cached family counts do not reconcile with frozen strict evaluation")
    families = []
    for family in sorted(set(membership.values())):
        subset = [x for x in pages if membership[x["dataset_page_id"]] == family]
        families.append(
            {
                "family_id": family,
                "institution_names": sorted({x["institution"] for x in subset}),
                "window_ids": sorted(x["dataset_page_id"] for x in subset),
                **aggregate(subset),
            }
        )
    return {
        "scope": "CACHED_EXPOSED_PARSER_FAMILY_BREAKDOWN_NOT_LEAVE_ONE_OUT",
        "families": families,
        "pooled": pooled,
        "macro": {
            key: mean_defined([x["rates"][key] for x in families]) for key in pooled["rates"]
        },
        "strict_counts_reconciled": True,
    }


# Fresh training for every TUNING family; diagnostics never enter folds.
def run_entry_folds(metadata: list[dict], examples: list[dict], membership: dict) -> dict:
    """Fresh training for every TUNING family; diagnostics never enter folds."""
    # Stop if the fold membership differs from transitive source dependencies.
    if membership != audit_split_metadata(metadata)["family_membership"]:
        raise ValueError("fold membership differs from transitive source dependencies")
    by_id = {x["dataset_page_id"]: x for x in metadata}
    for row in examples:
        page = by_id.get(row["dataset_page_id"])
        # Stop if the fold example differs from bound metadata.
        if page is None or any(row[k] != page[k] for k in ("learning_partition", "learning_group")):
            raise ValueError("fold example differs from bound metadata")
        role = evaluation_role(row["learning_partition"])
        # Each fold example must have the exposure permission expected by its partition.
        if row.get("evaluation_role", role) != role:
            raise ValueError("fold example exposure contradicts partition")
    tuning_pages = [x for x in metadata if evaluation_role(x["learning_partition"]) == "TUNING"]
    tuning = sorted(
        [x for x in examples if evaluation_role(x["learning_partition"]) == "TUNING"],
        key=lambda x: (x["dataset_page_id"], x["candidate_id"]),
    )
    folds, predictions, evaluated, decisions = [], [], [], []
    for family in sorted({membership[x["dataset_page_id"]] for x in tuning_pages}):
        train = [x for x in tuning if membership[x["dataset_page_id"]] != family]
        test = [x for x in tuning if membership[x["dataset_page_id"]] == family]
        # Equal weighting uses the connected family, not an alias's legacy group.
        fit_rows = [{**x, "learning_group": membership[x["dataset_page_id"]]} for x in train]
        fold = {
            "held_out_family": family,
            "train_families": sorted({membership[x["dataset_page_id"]] for x in train}),
            "train_window_ids": sorted(
                x["dataset_page_id"]
                for x in tuning_pages
                if membership[x["dataset_page_id"]] != family
            ),
            "test_window_ids": sorted(
                x["dataset_page_id"]
                for x in tuning_pages
                if membership[x["dataset_page_id"]] == family
            ),
            "train_candidate_ids": [[x["dataset_page_id"], x["candidate_id"]] for x in train],
            "test_candidate_ids": [[x["dataset_page_id"], x["candidate_id"]] for x in test],
            "train_label_counts": dict(Counter(str(x["label"]) for x in train)),
            "test_label_counts": dict(Counter(str(x["label"]) for x in test)),
            "independently_adjudicated_test_examples": sum(
                x["independently_adjudicated"] for x in test
            ),
            "fit_input_sha256": digest(fit_rows),
        }
        if not test:
            fold["status"] = "SKIPPED_NO_REVIEWED_CANDIDATES"
        elif {x["label"] for x in train} != {0, 1}:
            fold["status"] = "SKIPPED_TRAINING_MISSING_CLASS"
        else:
            weights = fit_ranker(fit_rows, **PARAMETERS)
            scores = [probability(x["features"], weights) for x in test]
            proposed = [x >= THRESHOLD for x in scores]
            fold.update(
                {
                    "status": "FRESH_FAMILY_EXCLUDED_WEIGHTS_EVALUATED",
                    "weights": weights,
                    "weights_sha256": digest(weights),
                    "baseline": classification_metrics(
                        test, [x["baseline_status"] == "INDEPENDENT_COURSE" for x in test]
                    ),
                    "ranker": classification_metrics(test, proposed),
                }
            )
            for row, score, decision in zip(test, scores, proposed, strict=True):
                predictions.append(
                    {
                        "dataset_page_id": row["dataset_page_id"],
                        "candidate_id": row["candidate_id"],
                        "held_out_family": family,
                        "label": row["label"],
                        "score": score,
                        "proposed_label": int(decision),
                        "score_is_calibrated_probability": False,
                        "changes_course_ownership": False,
                    }
                )
            evaluated.extend(test)
            decisions.extend(proposed)
        folds.append(fold)
    finished = [x for x in folds if "ranker" in x]
    pooled = {
        "baseline": classification_metrics(
            evaluated, [x["baseline_status"] == "INDEPENDENT_COURSE" for x in evaluated]
        ),
        "ranker": classification_metrics(evaluated, decisions),
    }
    return {
        "scope": "TUNING_ONLY_FRESH_ENTRY_RANKER_FOLDS_CONDITIONAL_ON_CANDIDATES",
        "folds": folds,
        "predictions": predictions,
        "pooled": pooled,
        "macro": {
            arm: {
                key: mean_defined([x[arm][key] for x in finished])
                for key in ("precision", "recall", "f1")
            }
            for arm in ("baseline", "ranker")
        },
        "paired_macro": {
            arm: {
                key: mean_defined(
                    [
                        x[arm][key]
                        if x["baseline"][key] is not None and x["ranker"][key] is not None
                        else None
                        for x in finished
                    ]
                )
                for key in ("precision", "recall", "f1")
            }
            for arm in ("baseline", "ranker")
        },
        "completed_folds": len(finished),
        "planned_folds": len(folds),
        "evaluated_examples": len(evaluated),
        "tuning_examples": len(tuning),
        "excluded_diagnostic_examples": len(examples) - len(tuning),
        "independently_adjudicated_tuning_examples": sum(
            x["independently_adjudicated"] for x in tuning
        ),
        "hyperparameters": {
            **PARAMETERS,
            "threshold": THRESHOLD,
            "weighting": "equal class, equal connected family within class",
        },
        "hyperparameters_or_threshold_selected_on_folds": False,
        "promotion_status": "EXPERIMENTAL_REVIEW_ONLY",
    }


# Compare the permitted development institutions in separate folds and report both model
# results and frozen-parser diagnostics.
def evaluate_institution_out(pilot: Path, evaluation: Path, output: Path) -> dict:
    metadata = load_exposed_split_metadata(pilot, evaluation, output)
    # Reject role/dependency violations before decoding any cached labels.
    audit_split_metadata(metadata)
    manifest = read_json(_metadata_file(evaluation, "evaluation_manifest.json"))
    # Reject an evaluation that opened protected material.
    if manifest.get("protected_evaluation_opened") is not False:
        raise ValueError("evaluation opened protected material")
    inputs = {}
    for name in ("page_metrics.jsonl", "entry_training_examples.jsonl"):
        path = _metadata_file(evaluation, name)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(path) != manifest["file_sha256"].get(name):
            raise ValueError("cached diagnostic checksum changed")
        inputs[name] = read_jsonl(path)
    pages, examples = inputs["page_metrics.jsonl"], inputs["entry_training_examples.jsonl"]
    audit = bind_records(metadata, pages, examples)
    parser = parser_family_diagnostics(pages, audit["family_membership"], manifest["overall"])
    entry = run_entry_folds(metadata, examples, audit["family_membership"])
    predictions = entry.pop("predictions")
    result = {
        "schema_version": VERSION,
        "status": "EXPOSED_INSTITUTION_OUT_EXPERIMENT_COMPLETE",
        "parser_diagnostics": parser,
        "entry_experiment": entry,
        "split_audit": audit,
        "generalization_claim_permitted": False,
        "changes_authoritative_parser": False,
        "protected_evaluation_opened": False,
        "limits": LIMITS,
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "institution_out.json", result)
    write_jsonl(output / "out_of_family_candidate_scores.jsonl", predictions)
    _viewer(output, result)
    write_json(
        output / "manifest.json",
        {
            "schema_version": VERSION,
            "protected_evaluation_opened": False,
            "source_or_review_answers_opened": False,
            "cached_exposed_candidate_labels_used": True,
            "input_sha256": {
                "pilot_manifest": sha256(_metadata_file(pilot, "pilot_manifest.json")),
                "metadata": sha256(_metadata_file(pilot, "selected_pages.jsonl")),
                "evaluation_manifest": sha256(
                    _metadata_file(evaluation, "evaluation_manifest.json")
                ),
                **{name: sha256(_metadata_file(evaluation, name)) for name in inputs},
            },
            "code_sha256": {
                name: sha256(Path(__file__).with_name(name))
                for name in (
                    "institution_out.py",
                    "entry_learning.py",
                    "evaluation_design.py",
                    "learning_evaluation.py",
                )
            },
            "file_sha256": artifact_hashes(output),
        },
    )
    return {
        "status": result["status"],
        "completed_entry_folds": entry["completed_folds"],
        "entry_pooled": entry["pooled"],
        "parser_counts": parser["pooled"]["counts"],
        "generalization_claim_permitted": False,
        "protected_evaluation_opened": False,
    }


# Build the HTML report and escape displayed source text.
def _viewer(output: Path, result: dict) -> None:
    # Escape displayed text so it cannot be interpreted as HTML markup.
    def esc(x):
        return html.escape(str(x))

    # Format a measured fraction as a percentage and show missing values explicitly.
    def pct(x):
        return "—" if x is None else f"{x:.1%}"

    # Show the count and its denominator, including cases where no rate can be calculated.
    def fraction(counts, name):
        return f"{counts[name + '_correct']}/{counts[name + '_eligible']}"

    parser, entry = result["parser_diagnostics"], result["entry_experiment"]
    names = {x["family_id"]: ", ".join(x["institution_names"]) for x in parser["families"]}
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(v)}</td>"
            for v in (
                names[x["family_id"]],
                ", ".join(x["evaluation_roles"]),
                x["windows"],
                x["counts"]["pages"],
                x["excluded_windows"],
                fraction(x["counts"], "complete_content"),
                pct(x["rates"]["complete_content_recall"]),
                fraction(x["counts"], "complete_all_fields"),
                x["counts"]["false_course_proposals"],
                x["counts"]["ambiguous_course_proposals"],
                x["counts"]["description_contamination"],
            )
        )
        + "</tr>"
        for x in parser["families"]
    )
    fold_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(v)}</td>"
            for v in (
                names[x["held_out_family"]],
                len(x["train_candidate_ids"]),
                len(x["test_candidate_ids"]),
                x["test_label_counts"].get("1", 0),
                x["status"],
                pct(x.get("baseline", {}).get("f1")),
                pct(x.get("ranker", {}).get("f1")),
                x.get("ranker", {}).get("fp", "—"),
                x.get("ranker", {}).get("fn", "—"),
            )
        )
        + "</tr>"
        for x in entry["folds"]
    )
    macro_rows = "".join(
        f"<tr><td>{esc(arm)}</td><td>{pct(entry['pooled'][arm]['f1'])}</td>"
        f"<td>{pct(entry['macro'][arm]['f1']['value'])}</td>"
        f"<td>{entry['macro'][arm]['f1']['defined_families']}/{entry['completed_folds']}</td></tr>"
        for arm in ("baseline", "ranker")
    )
    limits = "".join(f"<li>{esc(x)}</li>" for x in result["limits"])
    paired = entry["paired_macro"]
    text = f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Phase 14 — Institution family evaluation</title>
    <style>body{{font:16px/1.6 system-ui;max-width:1300px;margin:35px auto;padding:0 24px;
    color:#162b3b;background:#f6f8fa}}
    h1,h2{{line-height:1.2}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}
    th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #ddd}}th{{background:#deebf2}}
    .scroll{{overflow:auto}}.note{{padding:18px;background:#e8f1f6;border-left:5px solid #287393}}
    a{{color:#176295}}</style><h1>Phase 14 — Institution family evaluation</h1>
    <p class="note">{entry["planned_folds"]}-family candidate experiment
    and cached parser diagnostics.
    The reserved cohort remains sealed. This experiment does not establish untouched generalization.
    Models remain review proposals; no extraction behavior changes.</p>
    <h2>Existing parser: cached family breakdown</h2>
    <p>Complete core courses: {fraction(parser["pooled"]["counts"], "complete_content")}.
    All fields: {fraction(parser["pooled"]["counts"], "complete_all_fields")}.
    The strict counts reconcile exactly with the approved cached evaluation.
    These are exposed scores, not leave-one-out parser training.
    Zero denominators are undefined.</p>
    <div class="scroll"><table><tr><th>Institution names</th><th>Exposure</th><th>Windows</th>
    <th>Scored/failed</th><th>Excluded</th><th>Core exact</th><th>Core rate</th><th>All fields</th>
    <th>False</th><th>Ambiguous</th>
    <th>Contamination</th></tr>{rows}</table></div>
    <h2>Fresh entry-ranker weights with each TUNING family excluded</h2>
    <p>{entry["completed_folds"]}/{entry["planned_folds"]} completed folds;
    {entry["evaluated_examples"]} candidate labels evaluated once each;
    {entry["excluded_diagnostic_examples"]} exposed diagnostic labels excluded.
    Fixed 600 iterations, learning rate 0.3, regularization 0.02, threshold 0.5.
    {entry["independently_adjudicated_tuning_examples"]} TUNING labels carry
    independent-adjudication metadata.
    Candidate F1 does not measure complete-course accuracy.</p>
    <div class="scroll"><table><tr><th>Excluded family</th><th>Train n</th><th>Test n</th>
    <th>Positive n</th><th>Status</th><th>Baseline F1</th><th>Ranker F1</th><th>Ranker FP</th>
    <th>Ranker FN</th></tr>{fold_rows}</table></div>
    <p>Macro averages weight defined family metrics equally;
    pooled metrics sum out-of-family confusion counts.
    An all-negative family has no defined positive recall. It still tests false positives.</p>
    <table><tr><th>Arm</th><th>Pooled candidate F1</th><th>Macro family F1</th>
    <th>Defined F1 families</th></tr>{macro_rows}</table>
    <p>For a comparison using the same families with defined F1 in both arms:
    baseline {pct(paired["baseline"]["f1"]["value"])},
    ranker {pct(paired["ranker"]["f1"]["value"])}
    ({paired["baseline"]["f1"]["defined_families"]} common families).
    The pooled false-positive counts still include the all-negative family.</p>
    <p>Candidate false positives: baseline {entry["pooled"]["baseline"]["fp"]},
    ranker {entry["pooled"]["ranker"]["fp"]}. Candidate false negatives:
    baseline {entry["pooled"]["baseline"]["fn"]}, ranker {entry["pooled"]["ranker"]["fn"]}.
    These labels do not count new courses. The ranker remains experimental and is not activated.</p>
    <h2>Interpretation limits</h2><ul>{limits}</ul>
    <p>The suggestion to rotate institution families is appropriate for trained components.
    A poor institution score is a prompt for source review,
    not proof of a missing layout family.</p>
    <p><a href="institution_out.json">Full metrics and fold weights</a> ·
    <a href="out_of_family_candidate_scores.jsonl">Out-of-family candidate predictions</a> ·
    <a href="manifest.json">Input and code provenance</a> ·
    <a href="https://scikit-learn.org/stable/modules/generated/\
sklearn.model_selection.LeaveOneGroupOut.html">Grouped evaluation definition</a></p></html>"""
    (output / "index.html").write_text(text, encoding="utf-8")
