# Experiment with a small model that ranks possible course entries. Train only on permitted
# development examples, then compare its suggestions with the existing parser; the model does
# not approve courses.
"""Small transparent supervised entry ranker, separate from extraction truth."""

from __future__ import annotations

import html
import math
import os
from collections import Counter
from pathlib import Path

from .evaluation_design import evaluation_role
from .io import (
    artifact_hashes,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from .learning_evaluation import candidate_features

MODEL_VERSION = "structural-entry-logistic-v1"


# Calculate the experimental ranker output without changing course admission.
def probability(features: dict[str, float], weights: dict[str, float]) -> float:
    value = sum(weights.get(key, 0.0) * number for key, number in features.items())
    value = max(-35.0, min(35.0, value))
    return 1 / (1 + math.exp(-value))


# Fit TUNING only, balancing classes and institutions within each class.
def fit_ranker(
    rows: list[dict],
    *,
    iterations: int = 600,
    learning_rate: float = 0.3,
    regularization: float = 0.02,
) -> dict[str, float]:
    """Fit TUNING only, balancing classes and institutions within each class."""
    # Train the ranker only when permitted tuning examples are available.
    if not rows or any(
        x["learning_partition"] != "TUNING" or x.get("evaluation_role", "TUNING") != "TUNING"
        for x in rows
    ):
        raise ValueError("fit_ranker accepts nonempty TUNING examples only")
    # Entry learning requires both source-reviewed classes.
    if {x["label"] for x in rows} != {0, 1}:
        raise ValueError("entry learning requires both source-reviewed classes")
    weights = {key: 0.0 for row in rows for key in row["features"]}
    counts = Counter((x["label"], x["learning_group"]) for x in rows)
    groups = {label: {x["learning_group"] for x in rows if x["label"] == label} for label in (0, 1)}
    sample_weights = [
        0.5 / len(groups[x["label"]]) / counts[x["label"], x["learning_group"]] for x in rows
    ]
    for _ in range(iterations):
        gradient = {key: 0.0 for key in weights}
        for row, importance in zip(rows, sample_weights, strict=True):
            residual = (probability(row["features"], weights) - row["label"]) * importance
            for key, value in row["features"].items():
                gradient[key] += residual * value
        for key in weights:
            penalty = 0.0 if key == "bias" else regularization * weights[key]
            weights[key] -= learning_rate * (gradient[key] + penalty)
    return weights


# Count classification outcomes and report precision and recall with explicit denominators.
def classification_metrics(rows: list[dict], decisions: list[bool]) -> dict:
    counts = Counter()
    for row, decision in zip(rows, decisions, strict=True):
        counts[
            "tp"
            if row["label"] and decision
            else "fn"
            if row["label"]
            else "fp"
            if decision
            else "tn"
        ] += 1
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return {
        **{key: counts[key] for key in ("tp", "fp", "fn", "tn")},
        "examples": len(rows),
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


# Train the small entry ranker using only approved development examples and write its
# evaluation and model artifacts.
def train_entry_ranker(evaluation: Path, output: Path) -> dict:
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(evaluation, output, evaluation / "evaluation_manifest.json")
    manifest = read_json(evaluation / "evaluation_manifest.json")
    # Reject training inputs that opened protected evaluation material.
    if manifest["protected_evaluation_opened"] is not False:
        raise ValueError("training inputs opened protected evaluation")
    verify_files(evaluation, manifest["file_sha256"])
    examples_path = evaluation / "entry_training_examples.jsonl"
    rows = read_jsonl(examples_path)
    for row in rows:
        role = evaluation_role(row["learning_partition"])
        # Each training example's role must agree with its recorded partition.
        if row.get("evaluation_role", role) != role:
            raise ValueError("training example evaluation role contradicts its partition")
    tuning = [x for x in rows if x["learning_partition"] == "TUNING"]
    validation = [x for x in rows if x["learning_partition"] == "VALIDATION"]
    # Reject unknown learning partition.
    if len(tuning) + len(validation) != len(rows):
        raise ValueError("unknown learning partition")
    tuning_groups = {x["learning_group"] for x in tuning}
    # Keep an institution family entirely in one partition to avoid training on validation
    # templates.
    if tuning_groups & {x["learning_group"] for x in validation}:
        raise ValueError("institution family leaked across training and validation")
    allowed = {
        "bias",
        "header_supported",
        "title_present",
        "title_word_count",
        "detail_count",
        "context_confirmed",
        "contradiction_count",
    }
    for row in rows:
        # Restrict the entry ranker to the approved structural features.
        if any(
            key not in allowed
            and not key.startswith(("identifier:", "arrangement:", "context:", "signal:"))
            for key in row["features"]
        ):
            raise ValueError("entry ranker received a nonstructural feature")
        # Training labels must come from a reviewed heading or a complete reviewed course inventory.
        if row["label"] not in (0, 1) or row["label_basis"] not in {
            "SOURCE_REVIEWED_HEADER_ALIGNMENT",
            "COMPLETE_SOURCE_INVENTORY_NO_HEADER_AT_CANDIDATE",
        }:
            raise ValueError("training label lacks source-inventory provenance")
    weights = fit_ranker(tuning)
    prediction_rows = []
    for row in rows:
        score = probability(row["features"], weights)
        prediction_rows.append(
            {
                "dataset_page_id": row["dataset_page_id"],
                "candidate_id": row["candidate_id"],
                "learning_partition": row["learning_partition"],
                "evaluation_role": evaluation_role(row["learning_partition"]),
                "source_reviewed_label": row["label"],
                "entry_score": score,
                "score_is_calibrated_probability": False,
                "proposed_label_at_fixed_threshold": int(score >= 0.5),
                "status": "EXPERIMENTAL_REVIEW_PROPOSAL",
                "changes_course_ownership": False,
            }
        )
    metrics = {}
    for partition, subset in (("TUNING", tuning), ("VALIDATION", validation)):
        metrics[partition] = {
            "evaluation_role": evaluation_role(partition),
            "generalization_claim_permitted": False,
            "conditional_candidate_scope": (
                "Does not measure missed headers, course boundaries, fields or keyword ownership."
            ),
            "baseline": classification_metrics(
                subset, [x["baseline_status"] == "INDEPENDENT_COURSE" for x in subset]
            ),
            "ranker": classification_metrics(
                subset, [probability(x["features"], weights) >= 0.5 for x in subset]
            ),
        }
    model = {
        "schema_version": "course-parser-entry-ranker-v1",
        "version": MODEL_VERSION,
        "weights": weights,
        "decision_threshold": 0.5,
        "hyperparameters": {
            "iterations": 600,
            "learning_rate": 0.3,
            "regularization": 0.02,
            "weighting": "equal class, equal institution within class",
        },
        "threshold_selected_on_validation": False,
        "weights_fit_on_validation": False,
        "training_groups": sorted(tuning_groups),
        "training_examples": len(tuning),
        "training_label_counts": dict(Counter(str(x["label"]) for x in tuning)),
        "training_source": (
            "Source-reviewed DEVELOPMENT inventory alignment; no independent adjudication assumed"
        ),
        "input_sha256": sha256(examples_path),
        "evaluation_manifest_sha256": sha256(evaluation / "evaluation_manifest.json"),
        "promotion_status": "EXPERIMENTAL_REVIEW_ONLY",
        "evaluation_scope": "EXPOSED_DEVELOPMENT_DIAGNOSTIC",
        "generalization_claim_permitted": False,
        "changes_authoritative_ledger": False,
        "metrics": metrics,
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "entry_ranker.json", model)
    write_jsonl(output / "candidate_scores.jsonl", prediction_rows)
    write_json(
        output / "training_manifest.json",
        {
            "status": "EXPERIMENT_TRAINED",
            "model_version": MODEL_VERSION,
            "file_sha256": artifact_hashes(output),
            "fit_code_sha256": sha256(Path(__file__)),
            "protected_evaluation_opened": False,
            "metrics": metrics,
        },
    )
    return {
        "status": "EXPERIMENT_TRAINED",
        "training_examples": len(tuning),
        "validation_examples": len(validation),
        "validation_examples_scope": "EXPOSED_DIAGNOSTIC",
        "generalization_claim_permitted": False,
        "metrics": metrics,
        "promotion_status": "EXPERIMENTAL_REVIEW_ONLY",
    }


# Require identical reviewed pages, answers, and pilot for paired comparison.
def compare_learning_runs(before: Path, after: Path, output: Path) -> dict:
    """Require identical reviewed pages, answers, and pilot for paired comparison."""
    manifests = [read_json(root / "evaluation_manifest.json") for root in (before, after)]
    for root, manifest in zip((before, after), manifests, strict=True):
        verify_files(root, manifest["file_sha256"])
    for key in (
        "pilot_manifest_sha256",
        "truth_input_sha256",
        "evaluation_code_sha256",
        "partitions_scored",
    ):
        # Paired comparison requires the same pilot, answers, partitions and scoring code.
        if manifests[0].get(key) != manifests[1].get(key):
            raise ValueError(
                "paired comparison requires the same pilot, answers, partitions and scoring code"
            )
    pages = [
        {x["dataset_page_id"]: x for x in read_jsonl(root / "page_metrics.jsonl")}
        for root in (before, after)
    ]
    # Stop if the paired comparison page inventory changed.
    if set(pages[0]) != set(pages[1]):
        raise ValueError("paired comparison page inventory changed")
    differences = []
    for page_id in sorted(pages[0]):
        old, new = pages[0][page_id], pages[1][page_id]
        differences.append(
            {
                "dataset_page_id": page_id,
                "learning_partition": old.get("learning_partition"),
                "before_status": old["status"],
                "after_status": new["status"],
                "missed_courses_delta": new.get("missed_courses", 0) - old.get("missed_courses", 0),
                "false_course_proposals_delta": new.get("false_course_proposals", 0)
                - old.get("false_course_proposals", 0),
                "complete_content_delta": new.get("complete_content_correct", 0)
                - old.get("complete_content_correct", 0),
                "complete_all_fields_delta": new.get("complete_all_fields_correct", 0)
                - old.get("complete_all_fields_correct", 0),
            }
        )
    result = {
        "status": "PAIRED_DEVELOPMENT_COMPARISON",
        "before": manifests[0]["overall"],
        "after": manifests[1]["overall"],
        "before_manifest_sha256": sha256(before / "evaluation_manifest.json"),
        "after_manifest_sha256": sha256(after / "evaluation_manifest.json"),
        "by_partition": {
            part: {
                "before": manifests[0]["by_partition"][part],
                "after": manifests[1]["by_partition"][part],
            }
            for part in ("TUNING", "VALIDATION")
        },
        "error_counts": {
            "before": manifests[0]["error_counts"],
            "after": manifests[1]["error_counts"],
        },
        "promotion_status": "REQUIRES_SOURCE_REVIEW_AND_PROTECTED_EVALUATION",
    }
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "paired_page_changes.jsonl", differences)
    write_json(output / "comparison.json", result)
    _comparison_viewer(output, before, after, manifests, pages)
    return result


# Build a read-only report comparing experimental and baseline decisions.
def _comparison_viewer(output, before, after, manifests, pages):
    # Escape displayed text so it cannot be interpreted as HTML markup.
    def esc(value):
        return html.escape(str(value))

    names = {
        "matched_course_proposals": "Courses recovered",
        "gold_courses": "Reviewed courses",
        "complete_content_correct": "Complete code, title, description and boundaries",
        "confirmed_extent_exact_matched": "Exact blocks with confirmed boundaries",
        "description_exact_matched": "Exact descriptions",
        "complete_all_fields_correct": "Complete entries including credits and prerequisites",
        "false_course_proposals": "False course proposals",
        "ambiguous_course_proposals": "Ambiguous proposal alignments",
        "parser_failed_pages": "Failed reviewed pages",
        "course_precision_conservative": "Conservative course precision",
        "complete_content_recall": "Complete content recall",
    }

    # Build an HTML table and escape the text in its cells.
    def table(old, new):
        return (
            "<table><tr><th>Result</th><th>Before</th><th>After</th></tr>"
            + "".join(
                f"<tr><td>{esc(label)}</td><td>{esc(old.get(key))}</td><td>{esc(new.get(key))}</td></tr>"
                for key, label in names.items()
            )
            + "</table>"
        )

    body = table(manifests[0]["overall"], manifests[1]["overall"])
    for part in ("TUNING", "VALIDATION"):
        body += f"<h2>{part}</h2>" + table(
            manifests[0]["by_partition"][part], manifests[1]["by_partition"][part]
        )
    old_root = os.path.relpath(before, output)
    new_root = os.path.relpath(after, output)
    rows = []
    for pid in pages[0]:
        old, new = pages[0][pid], pages[1][pid]
        if new["status"] != "SCORED_DEVELOPMENT":
            continue
        rows.append(
            f"<tr><td>{esc(new['institution'])}, page {new['physical_page']}</td>"
            f"<td>{esc(new['learning_partition'])}</td>"
            f"<td>{old.get('matched_course_proposals', 0)} → "
            f"{new.get('matched_course_proposals', 0)} / {new['gold_courses']}</td>"
            f"<td>{old.get('complete_content_correct', 0)} → "
            f"{new.get('complete_content_correct', 0)}</td>"
            f"<td><a href='{esc(old_root)}/pages/{pid}/index.html'>Before</a> · "
            f"<a href='{esc(new_root)}/pages/{pid}/index.html'>After with source images</a>"
            "</td></tr>"
        )
    document = f"""<!doctype html><meta charset="utf-8"><title>Course parser improvement</title>
    <style>body{{font:17px system-ui;max-width:1200px;margin:35px auto;padding:20px;
    color:#182535;background:#f6f8fb}}table{{width:100%;border-collapse:collapse;background:white}}
    th,td{{padding:10px;border:1px solid #ccd4dc;text-align:left}}
    .note{{padding:20px;background:#fff0bd}}</style>
    <h1>Course parser: measured improvement</h1><p class="note">
    Same reviewed DEVELOPMENT pages, source answers and scoring code.
    Rules were developed using TUNING examples. VALIDATION is an exposed development diagnostic;
    these results do not establish performance on unseen catalogs.
    Original-hit ownership decisions remain pending. All parser records still require review.</p>
    {body}<h2>Inspect the result against the catalog</h2><table><tr><th>Source page</th>
    <th>Partition</th><th>Recovered courses</th><th>Complete content</th><th>Evidence</th></tr>
    {"".join(rows)}</table>
    <p>Complete content requires jointly correct code, title, description, confirmed exact block,
    page extent and continuation. Metadata absence must be source reviewed before earning credit
    in the all-fields metric. Pending pages are excluded from accuracy,
    and failed reviewed pages count as misses.</p>
    <p><a href="comparison.json">Machine-readable comparison</a> ·
    <a href="paired_page_changes.jsonl">Changes by page</a></p>"""
    (output / "index.html").write_text(document, encoding="utf-8")


# Rank pending source windows and exposed errors; never manufacture labels.
def build_review_queue(evaluation: Path, model_run: Path, output: Path) -> dict:
    """Rank pending source windows and exposed errors; never manufacture labels."""
    manifest = read_json(evaluation / "evaluation_manifest.json")
    verify_files(evaluation, manifest["file_sha256"])
    training_manifest = read_json(model_run / "training_manifest.json")
    verify_files(model_run, training_manifest["file_sha256"])
    model = read_json(model_run / "entry_ranker.json")
    predictions = Path(manifest["prediction_path"])
    prediction_manifest = read_json(predictions / "prediction_manifest.json")
    # Stop if the review queue prediction release changed.
    if sha256(predictions / "prediction_manifest.json") != manifest["prediction_manifest_sha256"]:
        raise ValueError("review queue prediction release changed")
    verify_files(predictions, prediction_manifest["file_sha256"])
    pilot = Path(manifest["pilot_path"])
    # Stop if the review queue pilot changed.
    if sha256(pilot / "pilot_manifest.json") != manifest["pilot_manifest_sha256"]:
        raise ValueError("review queue pilot changed")
    selected = {x["dataset_page_id"]: x for x in read_jsonl(pilot / "selected_pages.jsonl")}
    metrics = {x["dataset_page_id"]: x for x in read_jsonl(evaluation / "page_metrics.jsonl")}
    errors = Counter(x["dataset_page_id"] for x in read_jsonl(evaluation / "errors.jsonl"))
    rows = []
    for proposal in read_jsonl(predictions / "page_proposals.jsonl"):
        page_id = proposal["dataset_page_id"]
        item = selected[page_id]
        decisions = {x["candidate_id"]: x for x in proposal.get("decisions", [])}
        candidates = [
            x
            for x in proposal.get("candidates", [])
            if x["physical_page"] == item["central_physical_page"]
        ]
        uncertainty = [
            1
            - abs(
                2
                * probability(candidate_features(x, decisions[x["candidate_id"]]), model["weights"])
                - 1
            )
            for x in candidates
        ]
        pending = metrics[page_id]["status"] == "PENDING_SOURCE_REVIEW"
        rows.append(
            {
                "dataset_page_id": page_id,
                "institution": item["institution"],
                "source_pdf_checksum": item["source_pdf_checksum"],
                "physical_page": item["central_physical_page"],
                "original_page_task_id": item["original_page_task_id"],
                "original_hit_count": item["original_hit_count"],
                "learning_partition": item["learning_partition"],
                "learning_group": item["learning_group"],
                "status": metrics[page_id]["status"],
                "pending_source_review": pending,
                "known_error_count": errors[page_id],
                "candidate_count": len(candidates),
                "uncertainty_score": max(uncertainty, default=0),
                "priority_reason": "NEW_KEYWORD_SOURCE_REVIEW"
                if pending
                else "PARSER_FAILURE_SOURCE_INSPECTION",
                "viewer_path": str(evaluation.resolve() / "pages" / page_id / "index.html"),
                "source_review_decision": None,
            }
        )
    rows.sort(
        key=lambda x: (
            not x["pending_source_review"],
            -x["known_error_count"],
            -x["uncertainty_score"],
            x["dataset_page_id"],
        )
    )
    for rank, row in enumerate(rows, 1):
        row["review_rank"] = rank
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "review_queue.jsonl", rows)
    result = {
        "status": "REVIEW_QUEUE_READY",
        "pending_keyword_windows": sum(x["pending_source_review"] for x in rows),
        "diagnostic_windows": sum(not x["pending_source_review"] for x in rows),
        "sampling_policy": (
            "Pending keyword windows first, then known errors "
            "and uncalibrated candidate uncertainty. "
            "This active-review queue cannot estimate corpus-wide accuracy."
        ),
        "evaluation_manifest_sha256": sha256(evaluation / "evaluation_manifest.json"),
        "entry_ranker_sha256": sha256(model_run / "entry_ranker.json"),
        "authoritative_ledger_modified": False,
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "queue_manifest.json", result)
    return {key: result[key] for key in ("status", "pending_keyword_windows", "diagnostic_windows")}
