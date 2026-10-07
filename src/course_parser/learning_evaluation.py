# Compare saved parser predictions with permitted source reviews. Match proposed courses to
# reviewed entries, measure exact fields and complete records, and show the evidence in a
# local report.
"""Source-review import, conservative alignment, and DEVELOPMENT diagnostics."""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from .io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    read_selected_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from .learning import verify_learning_pilot
from .ownership_metrics import ownership_rates
from .reference import ReferenceProject

FIELDS = ("code", "title", "description", "credits", "prerequisites")
RESOLVED_FIELDS = {"OBSERVED_EXACT", "SAFE_NORMALIZED", "ABSENT_IN_SOURCE"}


# Normalize only the formatting allowed by the comparison rules, without rewriting the stored
# source text.
def canonical(text: str | None, *, code: bool = False) -> str | None:
    if text is None:
        return None
    value = " ".join(text.split())
    return "".join(value.split()).upper() if code else value


# Compare a recovered field with its source review using the approved exact-text rules.
def field_values_agree(actual: dict, expected: dict, name: str) -> bool:
    if expected["status"] == "ABSENT_IN_SOURCE":
        # An empty field can mean either "not recovered" or "confirmed absent in the source."
        # Only the second meaning earns credit for a correct absence.
        return actual["status"] == "ABSENT_IN_SOURCE" and actual["raw"] is None
    return canonical(actual["raw"], code=name == "code") == canonical(
        expected["raw"], code=name == "code"
    )


# Decide whether the source review contains enough information to score strict completion.
def complete_course_eligible(expected: dict, *, all_fields: bool = False) -> bool:
    """Unreviewed fields cannot silently shrink an end-to-end error denominator."""
    names = FIELDS if all_fields else ("code", "title", "description")
    return (
        expected["boundary_status"] == "CONFIRMED_BOUNDARY"
        and bool(expected.get("raw_course_block"))
        and all(expected[name]["status"] in RESOLVED_FIELDS for name in names)
        and expected["continuation_status"]
        in {"NONE", "CONFIRMED_CROSS_COLUMN", "CONFIRMED_CROSS_PAGE"}
    )


# Check whether the recovered course extent matches the reviewed source extent.
def boundaries_agree(actual: dict, expected: dict) -> bool:
    return (
        actual["boundary_status"] == "CONFIRMED_BOUNDARY"
        and actual["physical_pages"] == expected["physical_pages"]
        and actual["continuation_status"] == expected["continuation_status"]
        and canonical(actual["raw_course_block"]) == canonical(expected["raw_course_block"])
    )


# Check all required fields and source boundaries together; one failed requirement makes the
# record incomplete.
def complete_course_agrees(actual: dict, expected: dict, *, all_fields: bool = False) -> bool:
    names = FIELDS if all_fields else ("code", "title", "description")
    return (
        complete_course_eligible(expected, all_fields=all_fields)
        and boundaries_agree(actual, expected)
        and all(actual[name]["status"] in RESOLVED_FIELDS for name in names)
        and all(field_values_agree(actual[name], expected[name], name) for name in names)
        and (
            expected["description"]["status"] == "ABSENT_IN_SOURCE"
            or actual["description_status"] == "COMPLETE_DESCRIPTION"
        )
    )


# Measure box overlap relative to the smaller source area; return zero when geometry is
# missing.
def _box_overlap(a: dict | None, b: dict | None) -> float:
    if not a or not b:
        return 0.0
    overlap = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])) * max(
        0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    )
    area = min((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]))
    return overlap / area if area > 0 else 0.0


# Match headings only when their printed code or source location gives a unique pairing;
# ambiguous matches stay unresolved.
def align_headers(candidates: list[dict], gold: list[dict]) -> tuple[dict[str, str], set[str]]:
    """Mutually unique exact-code or source-box alignment. Never fuzzy text."""
    # Build possible matches in both directions. A match is usable only when neither heading
    # has a competing partner.
    edges = defaultdict(set)
    reverse = defaultdict(set)
    # Compare only headings from the same physical page, using printed identifiers or their
    # source locations.
    for candidate in candidates:
        for course in gold:
            if candidate["physical_page"] != course["header_physical_page"]:
                continue
            code = canonical(candidate["code_raw"], code=True)
            same_code = code is not None and code == canonical(course["code"]["raw"], code=True)
            geometry = any(
                _box_overlap(a.get("geometry"), b.get("geometry")) >= 0.5
                for a in candidate["evidence"]
                if a.get("node_id") in candidate["code_node_ids"]
                for b in course["code"]["evidence"]
            )
            if same_code or geometry:
                edges[candidate["candidate_id"]].add(course["gold_course_id"])
                reverse[course["gold_course_id"]].add(candidate["candidate_id"])
    matched = {
        cid: next(iter(ids))
        for cid, ids in edges.items()
        if len(ids) == 1 and len(reverse[next(iter(ids))]) == 1
    }
    ambiguous = set(edges) - set(matched)
    return matched, ambiguous


# Only import selected IDs after verifying the sealed proposal release.
def _review_subset(
    reference, pilot, predictions, review_path=None, truth_path=None, partitions=None
):
    """Only import selected IDs after verifying the sealed proposal release."""
    prediction_manifest = read_json(predictions / "prediction_manifest.json")
    # Predictions must have been saved before review answers were used for evaluation.
    if prediction_manifest["source_truth_read"] is not False:
        raise ValueError("baseline was not sealed independently of the answer key")
    verify_files(predictions, prediction_manifest["file_sha256"])
    # The saved predictions must belong to the sample being evaluated.
    if sha256(pilot / "pilot_manifest.json") != prediction_manifest["pilot_manifest_sha256"]:
        raise ValueError("prediction and pilot releases differ")
    manifest = read_json(pilot / "pilot_manifest.json")
    paths = manifest["snapshot_paths"]
    review_path = review_path or Path(paths["page_reviews.jsonl"])
    truth_path = truth_path or Path(paths["course_truth.jsonl"])
    # Default import must still match the frozen reconciled review snapshot.
    for path in (review_path, truth_path):
        expected = manifest["metadata_inputs"].get(str(path))
        # Stop if the source-reviewed snapshot changed since selection.
        if expected is not None and sha256(path) != expected:
            raise ValueError("source-reviewed snapshot changed since selection")
    annotation = importlib.import_module("catalog_mining.course_extraction_v3.annotation")
    selected = {x["dataset_page_id"]: x for x in read_jsonl(pilot / "selected_pages.jsonl")}
    review_ids = {
        key
        for key, item in selected.items()
        if partitions is None or item["learning_partition"] in partitions
    }
    raw_reviews = read_selected_jsonl(review_path, review_ids)
    reviews = {
        x["dataset_page_id"]: annotation.PageReview.model_validate(x).model_dump(mode="json")
        for x in raw_reviews
    }
    # Reject duplicate source page review.
    if len(reviews) != len(raw_reviews):
        raise ValueError("duplicate source page review")
    gold = [
        annotation.CourseTruth.model_validate(x).model_dump(mode="json")
        for x in read_selected_jsonl(truth_path, review_ids)
    ]
    # Reject duplicate source course truth.
    if len({x["gold_course_id"] for x in gold}) != len(gold):
        raise ValueError("duplicate source course truth")
    by_page = defaultdict(list)
    for course in gold:
        item = selected[course["dataset_page_id"]]
        # Course truth requires context outside materialized window.
        if not set(course["physical_pages"]).issubset(item["context_physical_pages"]):
            raise ValueError("course truth requires context outside materialized window")
        for field in (*FIELDS, "department", "section_context"):
            for anchor in course[field]["evidence"]:
                # Stop if the answer key field belongs to another PDF.
                if (
                    anchor["source_pdf_checksum"] != item["source_pdf_checksum"]
                    or anchor["source_document_id"] != item["source_document_id"]
                    or anchor["physical_page"] not in item["context_physical_pages"]
                ):
                    raise ValueError("answer key field belongs to another PDF")
        for anchor in (
            course["block_evidence"] + course["boundary_evidence"] + course["must_not_attach"]
        ):
            # Stop if the answer key block belongs to another PDF.
            if (
                anchor["source_pdf_checksum"] != item["source_pdf_checksum"]
                or anchor["source_document_id"] != item["source_document_id"]
                or anchor["physical_page"] not in item["context_physical_pages"]
            ):
                raise ValueError("answer key block belongs to another PDF")
        by_page[course["dataset_page_id"]].append(course)
    for page_id, review in reviews.items():
        if review["complete_course_inventory"]:
            image_channels = {x.casefold() for x in review["source_channels_reviewed"]}
            # A complete inventory needs a named reviewer who checked the source images.
            if not review["reviewer_id"] or not image_channels & {
                "rendered_source_images",
                "rendered_central_image",
            }:
                raise ValueError("complete course inventory lacks source-image review provenance")
            central = [x for x in by_page[page_id] if x["scope"] != "CONTEXT_ONLY"]
            context = [x for x in by_page[page_id] if x["scope"] == "CONTEXT_ONLY"]
            # Stop if the complete source course inventory is inconsistent.
            if (
                set(review["gold_course_ids"]) != {x["gold_course_id"] for x in central}
                or review["source_visible_course_count"] != len(central)
                or set(review["context_only_gold_course_ids"])
                != {x["gold_course_id"] for x in context}
            ):
                raise ValueError("complete source course inventory is inconsistent")
    return selected, reviews, by_page, {str(p): sha256(p) for p in (review_path, truth_path)}


# Describe a possible heading using structural cues, without keyword or institution shortcuts.
def candidate_features(candidate: dict, decision: dict) -> dict[str, float]:
    """Structural signals only; no keyword or institution features."""
    features = {
        "bias": 1.0,
        "header_supported": float(candidate["status"] == "SUPPORTED"),
        "title_present": float(bool(candidate["title_raw"])),
        "title_word_count": min(len((candidate["title_raw"] or "").split()), 30) / 30,
        "detail_count": min(len(decision["detail_node_ids"]), 20) / 20,
        "context_confirmed": float(decision["context_status"] == "CONFIRMED"),
        "contradiction_count": min(len(decision["contradictions"]), 10) / 10,
    }
    for key, value in (
        ("identifier", candidate["identifier_kind"]),
        ("arrangement", candidate["arrangement"]),
        ("context", decision["context_kind"]),
    ):
        features[f"{key}:{value}"] = 1.0
    for signal in set(candidate["structural_signals"] + decision["structural_signals"]):
        features[f"signal:{signal}"] = 1.0
    return features


# Match cached predictions to permitted source reviews, measure exact extraction and write the
# evidence report.
def score_learning(
    reference: ReferenceProject,
    pilot: Path,
    predictions: Path,
    output: Path,
    *,
    review_path: Path | None = None,
    truth_path: Path | None = None,
    partitions: tuple[str, ...] | None = None,
) -> dict:
    verify_learning_pilot(pilot)
    selected, reviews, by_page, truth_hashes = _review_subset(
        reference, pilot, predictions, review_path, truth_path, partitions
    )
    proposals = {x["dataset_page_id"]: x for x in read_jsonl(predictions / "page_proposals.jsonl")}
    # Stop if the predictions do not account for every pilot window.
    if set(proposals) != set(selected):
        raise ValueError("predictions do not account for every pilot window")
    records = defaultdict(list)
    for row in read_jsonl(predictions / "course_proposals.jsonl"):
        reference.models.CourseRecord.model_validate(row["course"])
        item = selected[row["dataset_page_id"]]
        # Stop if the course proposal belongs to another source window.
        if (
            row["course"]["source"]["source_pdf_checksum"] != item["source_pdf_checksum"]
            or row["course"]["source"]["source_document_id"] != item["source_document_id"]
            or not set(row["course"]["physical_pages"]).issubset(item["context_physical_pages"])
        ):
            raise ValueError("course proposal belongs to another source window")
        central = row["header_physical_page"] == item["central_physical_page"]
        # Stop if the course proposal central/context scope differs from header page.
        if (row["scope"] == "CENTRAL") != central:
            raise ValueError("course proposal central/context scope differs from header page")
        if row["scope"] == "CENTRAL":
            records[row["dataset_page_id"]].append(row)
    results, errors, training = [], [], []
    imported_reviews, imported_courses = [], []
    for page_id, item in selected.items():
        if partitions is not None and item["learning_partition"] not in partitions:
            results.append({"dataset_page_id": page_id, "status": "PARTITION_NOT_SCORED"})
            continue
        review = reviews.get(page_id)
        if review:
            imported_reviews.append(review)
            imported_courses.extend(by_page[page_id])
        row = {
            "dataset_page_id": page_id,
            "institution": item["institution"],
            "physical_page": item["central_physical_page"],
            "pilot_role": item["pilot_role"],
            "learning_partition": item["learning_partition"],
            "learning_group": item["learning_group"],
            "source_class": item["source_class"],
            "era": item["era"],
        }
        if (
            not review
            or not review["complete_course_inventory"]
            or review["status"] == "SOURCE_INPUT_INSUFFICIENT"
        ):
            results.append(
                {
                    **row,
                    "status": "SOURCE_INPUT_INSUFFICIENT"
                    if review and review["status"] == "SOURCE_INPUT_INSUFFICIENT"
                    else "PENDING_SOURCE_REVIEW",
                }
            )
            continue
        gold = [x for x in by_page[page_id] if x["scope"] == "HEADER_STARTS_ON_CENTRAL_PAGE"]
        proposal = proposals[page_id]
        complete_denominators = {
            "source_boundary_eligible": sum(
                x["boundary_status"] == "CONFIRMED_BOUNDARY" for x in gold
            ),
            "source_description_eligible": sum(
                x["description"]["status"] in RESOLVED_FIELDS for x in gold
            ),
            "zero_course_pages": int(not gold),
            "complete_content_eligible": sum(complete_course_eligible(x) for x in gold),
            "complete_all_fields_eligible": sum(
                complete_course_eligible(x, all_fields=True) for x in gold
            ),
        }
        if proposal["status"] != "PROPOSED":
            results.append(
                {
                    **row,
                    "status": "PARSER_STAGE_FAILED",
                    "gold_courses": len(gold),
                    "missed_courses": len(gold),
                    "matched_headers": 0,
                    "matched_course_proposals": 0,
                    "central_course_proposals": 0,
                    **complete_denominators,
                }
            )
            errors.append({**row, "error_type": "PARSER_STAGE_FAILED"})
            continue
        candidates = [
            x
            for x in proposal["candidates"]
            if x["physical_page"] == item["central_physical_page"] and x["status"] != "REJECTED"
        ]
        decisions = {x["candidate_id"]: x for x in proposal["decisions"]}
        matched, ambiguous = align_headers(candidates, gold)
        gold_by_id = {x["gold_course_id"]: x for x in gold}
        course_by_candidate = {x["course"]["header_candidate_id"]: x for x in records[page_id]}
        matched_records = {cid: gid for cid, gid in matched.items() if cid in course_by_candidate}
        # Any reviewed course without a uniquely matched record still counts as missed, even
        # if its heading was detected.
        missed = set(gold_by_id) - set(matched_records.values())
        # Separate genuine mismatches from ambiguous alignments; uncertain matches must not
        # become known false courses.
        false_records = set(course_by_candidate) - set(matched_records) - ambiguous
        for gid in missed:
            candidate_id = next((cid for cid, value in matched.items() if value == gid), None)
            kind = "MISSED_HEADER" if candidate_id is None else "ENTRY_WITHHELD"
            errors.append(
                {
                    **row,
                    "error_type": kind,
                    "gold_course_id": gid,
                    "candidate_id": candidate_id,
                    "expected_code": gold_by_id[gid]["code"]["raw"],
                    "expected_title": gold_by_id[gid]["title"]["raw"],
                }
            )
        for cid in false_records:
            errors.append(
                {
                    **row,
                    "error_type": "FALSE_COURSE_PROPOSAL",
                    "candidate_id": cid,
                    "proposed_code": course_by_candidate[cid]["course"]["code"]["raw"],
                }
            )
        # Measure only uniquely matched records here. Missing courses remain in the overall
        # denominators.
        metrics = Counter()
        for cid, gid in matched_records.items():
            actual = course_by_candidate[cid]["course"]
            expected = gold_by_id[gid]
            metrics["complete_content_correct"] += complete_course_agrees(actual, expected)
            metrics["complete_all_fields_correct"] += complete_course_agrees(
                actual, expected, all_fields=True
            )
            # Check each field separately so a partly correct course can be diagnosed without
            # being called complete.
            for name in FIELDS:
                # An unanswered source field is not evidence that the parser got it wrong;
                # keep it out of that field's scored opportunities.
                if expected[name]["status"] not in RESOLVED_FIELDS:
                    metrics[f"{name}_source_unresolved"] += 1
                    continue
                metrics[f"{name}_eligible_matched"] += 1
                same = field_values_agree(actual[name], expected[name], name)
                metrics[f"{name}_exact_matched"] += same
                if not same:
                    errors.append(
                        {
                            **row,
                            "error_type": "FIELD_MISMATCH",
                            "field": name,
                            "candidate_id": cid,
                            "gold_course_id": gid,
                            "expected": expected[name]["raw"],
                            "proposed": actual[name]["raw"],
                            "expected_status": expected[name]["status"],
                            "proposed_status": actual[name]["status"],
                        }
                    )
            if expected["boundary_status"] == "CONFIRMED_BOUNDARY":
                metrics["boundary_eligible_matched"] += 1
                same = canonical(actual["raw_course_block"]) == canonical(
                    expected["raw_course_block"]
                )
                metrics["block_exact_matched"] += same
                metrics["confirmed_extent_exact_matched"] += boundaries_agree(actual, expected)
                if not same:
                    errors.append(
                        {
                            **row,
                            "error_type": "BLOCK_EXTENT_MISMATCH",
                            "candidate_id": cid,
                            "gold_course_id": gid,
                        }
                    )
            contaminants = [
                x["observed_text"]
                for x in expected["must_not_attach"]
                if canonical(x["observed_text"])
                and canonical(x["observed_text"]) in (canonical(actual["description"]["raw"]) or "")
            ]
            if contaminants:
                metrics["description_contamination"] += 1
                errors.append(
                    {
                        **row,
                        "error_type": "DESCRIPTION_CONTAMINATION",
                        "candidate_id": cid,
                        "gold_course_id": gid,
                        "forbidden_quotes": contaminants,
                    }
                )
        # Conservative candidate labels for an experimental entry classifier.
        # Unmatched candidates near any gold header remain unlabeled.
        # Compare only headings from the same physical page, using printed identifiers or
        # their source locations.
        for candidate in candidates:
            cid = candidate["candidate_id"]
            near_gold = any(
                _box_overlap(a.get("geometry"), b.get("geometry")) > 0
                for a in candidate["evidence"]
                for course in gold
                for b in course["code"]["evidence"] + course["title"]["evidence"]
            )
            if cid in ambiguous or (cid not in matched and near_gold):
                continue
            training.append(
                {
                    **row,
                    "candidate_id": cid,
                    "label": int(cid in matched),
                    "label_basis": "SOURCE_REVIEWED_HEADER_ALIGNMENT"
                    if cid in matched
                    else "COMPLETE_SOURCE_INVENTORY_NO_HEADER_AT_CANDIDATE",
                    "gold_course_id": matched.get(cid),
                    "reviewer_id": review["reviewer_id"],
                    "independently_adjudicated": bool(
                        review["adjudicator_id"] or review["second_reviewer_id"]
                    ),
                    "features": candidate_features(candidate, decisions[cid]),
                    "baseline_status": decisions[cid]["status"],
                    "source_pdf_checksum": item["source_pdf_checksum"],
                    "source_document_id": item["source_document_id"],
                    "candidate_evidence": candidate["evidence"],
                }
            )
        results.append(
            {
                **row,
                "status": "SCORED_DEVELOPMENT",
                "gold_courses": len(gold),
                "central_course_proposals": len(course_by_candidate),
                "ambiguous_course_proposals": len(set(course_by_candidate) & ambiguous),
                "candidate_headers": len(candidates),
                "matched_headers": len(matched),
                "matched_course_proposals": len(matched_records),
                "missed_courses": len(missed),
                "false_course_proposals": len(false_records),
                "ambiguous_alignment": len(ambiguous),
                "unresolved_entries": sum(
                    decisions[x["candidate_id"]]["status"] == "UNRESOLVED" for x in candidates
                ),
                "false_proposals_on_zero_course_pages": len(course_by_candidate) if not gold else 0,
                "clean_zero_course_pages": int(not gold and not course_by_candidate),
                **complete_denominators,
                **metrics,
            }
        )
    scored = [x for x in results if x["status"] == "SCORED_DEVELOPMENT"]
    eligible = [x for x in results if x["status"] in {"SCORED_DEVELOPMENT", "PARSER_STAGE_FAILED"}]

    # Combine comparable counts and compute only ratios with defined denominators.
    def aggregate(rows):
        counts = {
            key: sum(x.get(key, 0) for x in rows)
            for key in (
                "source_boundary_eligible",
                "source_description_eligible",
                "zero_course_pages",
                "false_proposals_on_zero_course_pages",
                "clean_zero_course_pages",
                "gold_courses",
                "candidate_headers",
                "matched_headers",
                "matched_course_proposals",
                "central_course_proposals",
                "ambiguous_course_proposals",
                "missed_courses",
                "false_course_proposals",
                "ambiguous_alignment",
                "unresolved_entries",
                "description_contamination",
                "boundary_eligible_matched",
                "block_exact_matched",
                "confirmed_extent_exact_matched",
                "complete_content_eligible",
                "complete_content_correct",
                "complete_all_fields_eligible",
                "complete_all_fields_correct",
            )
        }
        counts["pages"] = len(rows)
        counts["parser_failed_pages"] = sum(x["status"] == "PARSER_STAGE_FAILED" for x in rows)
        counts["header_recall"] = (
            counts["matched_headers"] / counts["gold_courses"] if counts["gold_courses"] else None
        )
        counts["course_recall"] = (
            counts["matched_course_proposals"] / counts["gold_courses"]
            if counts["gold_courses"]
            else None
        )
        denominator = counts["matched_course_proposals"] + counts["false_course_proposals"]
        counts["course_precision"] = (
            counts["matched_course_proposals"] / denominator if denominator else None
        )
        counts["course_precision_note"] = (
            "Ambiguous proposals are counted separately; precision excludes unresolved alignments. "
            "Zero measured contamination with zero matched courses "
            "is not evidence of clean extraction."
        )
        counts["course_precision_conservative"] = (
            counts["matched_course_proposals"] / counts["central_course_proposals"]
            if counts["central_course_proposals"]
            else None
        )
        for name in ("complete_content", "complete_all_fields"):
            counts[name + "_recall"] = (
                counts[name + "_correct"] / counts[name + "_eligible"]
                if counts[name + "_eligible"]
                else None
            )
        counts["complete_course_definition"] = (
            "Joint exact code/title/description, complete description, confirmed exact owned "
            "block, page extent and continuation. all_fields requires exact credits/prerequisites "
            "including source-reviewed absence. Unrecovered absence earns no credit. "
            "Missing courses and failed pages remain in eligible denominators."
        )
        for field in FIELDS:
            eligible = sum(x.get(f"{field}_eligible_matched", 0) for x in rows)
            exact = sum(x.get(f"{field}_exact_matched", 0) for x in rows)
            counts[f"{field}_eligible_matched"] = eligible
            counts[f"{field}_exact_matched"] = exact
            counts[f"{field}_exact_rate_conditional"] = exact / eligible if eligible else None
        counts["ownership_metrics"] = ownership_rates(counts)
        return counts

    summary = {
        "schema_version": "course-parser-learning-evaluation-v2",
        "status": "DIAGNOSTIC_READY",
        "scope": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_NOT_FINAL_GENERALIZATION_ACCURACY",
        "truth_quality": (
            "Reused source reviews; reviewer and adjudication status retained per page. "
            "No independent gold certification is implied."
        ),
        "prediction_path": str(predictions.resolve()),
        "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "pilot_path": str(pilot.resolve()),
        "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        "truth_input_sha256": truth_hashes,
        "protected_evaluation_opened": False,
        "partitions_scored": list(partitions) if partitions else ["TUNING", "VALIDATION"],
        "scored_pages": len(scored),
        "pending_pages": sum(x["status"] == "PENDING_SOURCE_REVIEW" for x in results),
        "source_insufficient_pages": sum(
            x["status"] == "SOURCE_INPUT_INSUFFICIENT" for x in results
        ),
        "parser_failed_pages": sum(x["status"] == "PARSER_STAGE_FAILED" for x in results),
        "overall": aggregate(eligible),
        "by_partition": {
            part: aggregate([x for x in eligible if x["learning_partition"] == part])
            for part in ("TUNING", "VALIDATION")
        },
        "by_source_class": {
            part: aggregate([x for x in eligible if x["source_class"] == part])
            for part in sorted({x["source_class"] for x in eligible})
        },
        "by_review_role": {
            role: aggregate([x for x in eligible if x["pilot_role"] == role])
            for role in sorted({x["pilot_role"] for x in eligible})
        },
        "error_counts": dict(Counter(x["error_type"] for x in errors)),
        "training_candidates": len(training),
        "independently_adjudicated_pages": sum(
            bool(
                reviews[x["dataset_page_id"]]["adjudicator_id"]
                or reviews[x["dataset_page_id"]]["second_reviewer_id"]
            )
            for x in scored
        ),
        "original_hits_pending": read_json(pilot / "pilot_manifest.json")["original_physical_hits"],
        "keyword_ownership_accuracy": None,
        "keyword_ownership_note": (
            "Course proposals contain exact TITLE/DESCRIPTION matches. Original-hit dispositions "
            "are still blank and are not inferred from match text."
        ),
        "evaluation_code_sha256": sha256(Path(__file__)),
        "promotion_status": "NOT_APPROVED_FOR_PRODUCTION",
    }
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "imported_page_reviews.jsonl", imported_reviews)
    write_jsonl(output / "imported_course_truth.jsonl", imported_courses)
    write_jsonl(output / "page_metrics.jsonl", results)
    write_jsonl(output / "errors.jsonl", errors)
    write_jsonl(output / "entry_training_examples.jsonl", training)
    _write_viewer(output, pilot, selected, reviews, by_page, records, results, errors, summary)
    summary["file_sha256"] = artifact_hashes(output)
    write_json(output / "evaluation_manifest.json", summary)
    return {
        key: summary[key]
        for key in (
            "status",
            "scored_pages",
            "pending_pages",
            "overall",
            "error_counts",
            "training_candidates",
        )
    }


# Replace reviewer names in report copies with stable neutral identifiers.
def report_review_provenance(value):
    # Copy nested lists and records so the original review evidence stays intact.
    if isinstance(value, list):
        return [report_review_provenance(item) for item in value]
    # Visit each field when the value is a review record.
    if isinstance(value, dict):
        display = {}
        # Preserve every field; only reviewer identity values receive display aliases.
        for key, item in value.items():
            # Create an alias only for a nonempty reviewer identifier.
            if key in {"reviewer_id", "second_reviewer_id", "adjudicator_id"} and item:
                digest = hashlib.sha256(str(item).encode("utf-8")).hexdigest()[:12]
                display[key] = f"reviewer-{digest}"
            # Copy every other value recursively without changing its meaning.
            else:
                display[key] = report_review_provenance(item)
        return display
    return value


# Standalone review UI with real images and explicitly separate channels.
def _write_viewer(output, pilot, selected, reviews, gold, records, results, errors, summary):
    """Standalone review UI with real images and explicitly separate channels."""

    # Escape displayed text so it cannot be interpreted as HTML markup.
    def esc(value):
        return html.escape(str(value if value is not None else ""))

    style = """<style>
    body{font:16px system-ui;margin:30px auto;max-width:1500px;
    background:#f5f6fa;color:#182535}a{color:#005c9d}
    table{border-collapse:collapse;width:100%;background:white}
    td,th{padding:10px;border:1px solid #ccc;text-align:left}
    .window{display:grid;grid-template-columns:55% 45%;gap:18px}
    img{max-width:100%;border:1px solid #bbb}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:14px}
    .badge{background:#fff1bf;padding:12px}details{margin:12px 0}
    @media(max-width:900px){.window{display:block}}</style>"""
    nav = []
    error_by_page = defaultdict(list)
    for error in errors:
        error_by_page[error["dataset_page_id"]].append(error)
    metrics = {x["dataset_page_id"]: x for x in results}
    hits_by_page = defaultdict(list)
    for row in read_jsonl(pilot / "original_hits.jsonl"):
        hits_by_page[row["dataset_page_id"]].append(row["original_hit"])
    for name in (
        "page_review_template.jsonl",
        "course_truth_template.jsonl",
        "review_schema.json",
        "hit_disposition_template.csv",
    ):
        if (pilot / name).is_file():
            shutil.copyfile(pilot / name, output / name)
    for page_id, item in selected.items():
        page_dir = output / "pages" / page_id
        page_dir.mkdir(parents=True)
        source = read_json(contained_file(pilot, item["packet_path"]) / "source_reference.json")
        image_html = []
        for asset in source["page_artifacts"]:
            number = asset["physical_page"]
            name = f"page_{number:05d}.png"
            shutil.copyfile(asset["image_path"], page_dir / name)
            central = number == item["central_physical_page"]
            tag = (
                f"<p>Physical page {number} {'— CENTRAL' if central else '— CONTEXT'}</p>"
                f"<img src='{name}' alt='Catalog physical page {number}'>"
            )
            image_html.append((central, tag))
        images = "".join(tag for central, tag in image_html if central)
        images += (
            "<details><summary>Previous and next pages</summary>"
            + "".join(tag for central, tag in image_html if not central)
            + "</details>"
        )
        truth = json.dumps(report_review_provenance(gold[page_id]), ensure_ascii=False, indent=2)
        actual = json.dumps(records[page_id], ensure_ascii=False, indent=2)
        error_text = json.dumps(error_by_page[page_id], ensure_ascii=False, indent=2)
        review_text = json.dumps(
            report_review_provenance(reviews.get(page_id, {"status": "NOT_REVIEWED"})),
            ensure_ascii=False,
            indent=2,
        )
        source_text = "\n\n".join(
            f"PHYSICAL PAGE {asset['physical_page']}\n"
            + read_json(Path(asset["source_text_path"]))["raw_text"]
            for asset in source["page_artifacts"]
        )
        original_hit_text = json.dumps(hits_by_page[page_id], ensure_ascii=False, indent=2)
        document = f"""<!doctype html><meta charset='utf-8'>
        <title>Catalog learning review</title>{style}
        <a href='../../index.html'>← All windows</a>
        <h1>{esc(item["institution"])} · {item["catalog_year"]}
        · physical {item["central_physical_page"]}</h1>
        <p class='badge'>{esc(metrics[page_id]["status"])}
        · {esc(item["learning_partition"])} · DEVELOPMENT only</p>
        <p>Inspect the central image first. Neighbors establish boundaries.
        Model proposals never become reviewed answers automatically.</p>
        <div class='window'><div>{images}</div><div><h2>Errors to inspect</h2>
        <pre>{esc(error_text)}</pre>
        <details><summary>Existing source review and reviewer provenance</summary>
        <pre>{esc(review_text)}</pre></details>
        <details><summary>Source-reviewed course answers</summary>
        <pre>{esc(truth)}</pre></details>
        <details><summary>Model course proposals</summary><pre>{esc(actual)}</pre></details>
        <details><summary>Frozen extracted text — separate from the printed image</summary>
        <pre>{esc(source_text)}</pre></details>
        <details><summary>Original keyword IDs and exact source offsets
        — decisions pending</summary>
        <pre>{esc(original_hit_text)}</pre></details>
        <p>Submit typed page/course records through score-learning with --page-reviews
        and --course-truth. Preserve source evidence and unresolved fields.</p></div></div>"""
        (page_dir / "index.html").write_text(document, encoding="utf-8")
        displayed_errors = (
            len(error_by_page[page_id])
            if metrics[page_id]["status"] == "SCORED_DEVELOPMENT"
            else "—"
        )
        nav.append(
            f"<tr><td><a href='pages/{page_id}/index.html'>{esc(item['institution'])}</a></td>"
            f"<td>{item['catalog_year']}</td><td>{item['central_physical_page']}</td>"
            f"<td>{esc(item['pilot_role'])}</td><td>{esc(item['learning_partition'])}</td>"
            f"<td>{esc(metrics[page_id]['status'])}</td>"
            f"<td>{displayed_errors}</td></tr>"
        )
    document = f"""<!doctype html><meta charset='utf-8'>
    <title>Course parser learning pilot</title>{style}
    <h1>Course parser · source-reviewed learning pilot</h1>
    <p class='badge'>Existing DEVELOPMENT reviews provide diagnostics.
    These results do not establish independent test accuracy.</p>
    <p>{summary["scored_pages"]} reviewed windows scored;
    {summary["pending_pages"]} additional keyword windows await review.
    Institution families stay together in TUNING or VALIDATION.</p>
    <p><strong>{summary["overall"]["gold_courses"]} source-reviewed courses →
    {summary["overall"]["matched_headers"]} uniquely matched header candidates →
    {summary["overall"]["matched_course_proposals"]} matched course records.</strong></p>
    <p>The error report distinguishes missed headers from headers whose course status
    remains unresolved. Unreviewed pages contribute no training labels or accuracy scores.</p>
    <details><summary>Detailed diagnostic counts</summary>
    <pre>{esc(json.dumps(summary["overall"], indent=2))}</pre></details>
    <p>Original hits remain pending: {summary["original_hits_pending"]}.
    Review every course on each central page, including courses without keywords.</p>
    <p><a href='page_review_template.jsonl'>Blank page reviews</a> ·
    <a href='review_schema.json'>Course and page annotation schemas</a> ·
    <a href='hit_disposition_template.csv'>Blank original-hit dispositions</a></p>
    <table><thead><tr><th>Institution</th><th>Year</th><th>Page</th><th>Sample role</th>
    <th>Learning role</th><th>Status</th><th>Errors</th></tr></thead>
    <tbody>{"".join(nav)}</tbody></table>"""
    (output / "index.html").write_text(document, encoding="utf-8")
