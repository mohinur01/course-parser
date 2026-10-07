# Create blank annotation packets for labeling the roles of source text. Check completed
# reviews for source coverage, legal field transitions and the required independent review.
"""Blank source-only sequence annotation packets and a CRF readiness audit.

Course-level reviews and parser labels are never converted into training labels.
This command only prepares TUNING evidence; an independent sequence validation
cohort is still required before selecting or claiming a useful CRF.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections import Counter
from pathlib import Path

from .io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from .learning import verify_learning_pilot
from .omission_review import _reject_reserved_paths
from .sequence_constraints import CONTENT, HEADER, ROLES, SequenceState, SpanObservation, transition

VERSION = "source-sequence-annotations-v2"
LABELS = ROLES | {"NONCOURSE", "SECTION_CONTEXT", "PAGE_FURNITURE", "SOURCE_INSUFFICIENT"}
BOUNDARIES = {"SAME_COURSE", "NEXT_COURSE", "EXIT_COURSE", "OUTSIDE_COURSE", "UNCERTAIN"}
CUES = {
    "EXACT_HEADER",
    "PRINTED_CREDIT",
    "PRINTED_PREREQUISITE",
    "NARRATIVE_RESTART",
    "CONTINUATION",
    "NONE",
}


# Write blank annotation packets that preserve every source text unit and its original
# location.
def export_sequence_templates(pilot: Path, predictions: Path, output: Path) -> dict:
    _reject_reserved_paths(pilot, predictions, output)
    verify_learning_pilot(pilot)
    prediction = read_json(predictions / "prediction_manifest.json")
    # Create annotation templates from saved predictions generated without review answers.
    if (
        prediction.get("source_truth_read") is not False
        or prediction.get("protected_evaluation_opened") is not False
    ):
        raise ValueError("sequence templates require sealed source-only predictions")
    # Stop if the prediction cohort differs from source pilot.
    if prediction["pilot_manifest_sha256"] != sha256(pilot / "pilot_manifest.json"):
        raise ValueError("prediction cohort differs from source pilot")
    verify_files(predictions, prediction["file_sha256"])
    selected = {s["dataset_page_id"]: s for s in read_jsonl(pilot / "selected_pages.jsonl")}
    layout_module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    models = importlib.import_module("catalog_mining.course_extraction_v3.models")
    from .contextual import PageFlow

    rows = []
    for page in read_jsonl(predictions / "page_proposals.jsonl"):
        item = selected[page["dataset_page_id"]]
        if item["learning_partition"] != "TUNING" or page["status"] != "PROPOSED":
            continue
        units, page_orders = [], []
        for raw in page["layouts"]:
            proposal = layout_module.PageLayoutProposal.model_validate(raw)
            # Stop if the sequence source belongs to another document.
            if (
                proposal.source_pdf_checksum != item["source_pdf_checksum"]
                or proposal.source_document_id != item["source_document_id"]
            ):
                raise ValueError("sequence source belongs to another document")
            graph = layout_module.proposal_to_page_graph(proposal)
            flow = PageFlow(graph, models)
            usable = proposal.status == "COMPLETE"
            page_orders.append(
                {
                    "physical_page": graph.physical_page,
                    "order_confirmed": False,
                    "proposal_order_usable": usable,
                }
            )
            for node in sorted(
                graph.nodes, key=lambda n: (flow.order.get(n.node_id, 10**9), n.node_id)
            ):
                if not node.raw_text:
                    continue
                units.append(
                    {
                        "node_id": node.node_id,
                        "physical_page": graph.physical_page,
                        "text_span": {"start": 0, "end": len(node.raw_text)},
                        "raw_text": node.raw_text,
                        "evidence": [a.model_dump(mode="json") for a in node.evidence],
                        "label": None,
                        "owner_start_node_id": None,
                        "boundary_after": None,
                        "transition_cue": None,
                    }
                )
        rows.append(
            {
                "schema_version": VERSION,
                "dataset_page_id": page["dataset_page_id"],
                "source_document_id": item["source_document_id"],
                "source_pdf_checksum": item["source_pdf_checksum"],
                "learning_partition": "TUNING",
                "learning_group": item["learning_group"],
                "central_physical_page": item["central_physical_page"],
                "context_physical_pages": item["context_physical_pages"],
                "source_packet_path": str(contained_file(pilot, item["packet_path"])),
                "page_orders": page_orders,
                "review_status": "PENDING",
                "reviewer": None,
                "adjudicator": None,
                "source_order_review": None,
                "units": units,
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "sequence_template.jsonl", rows)
    write_json(
        output / "label_contract.json",
        {
            "schema_version": VERSION,
            "labels": sorted(LABELS),
            "boundary_after": sorted(BOUNDARIES),
            "transition_cues": sorted(CUES),
            "instructions": [
                "Inspect source images. Split mixed-field nodes into contiguous character spans.",
                "Preserve every source character; do not correct OCR or join text across nodes.",
                "Use the first header node as owner_start_node_id, including continuations.",
                "Assign noncourse/reference lists and source-insufficient spans explicitly.",
                "Labels are blank. Parser predictions are proposals, never gold answers.",
                "ADJUDICATED requires two distinct human reviewers and confirmed source order.",
                "Keep institution families together. Obtain independent sequence validation data.",
                "Parser COMPLETE order is provisional, never human-confirmed source order.",
                "Freeze page_orders; attach source_order_review for the exported order digest.",
                "Confirm exported order against images; if wrong, version source before labeling.",
                "UNCERTAIN remains PENDING; SOURCE_INSUFFICIENT excludes the whole window.",
                "Keep owners/boundaries separate from field labels; never insert course tokens.",
            ],
        },
    )
    result = {
        "schema_version": VERSION,
        "status": "BLANK_TUNING_TEMPLATES_READY",
        "windows": len(rows),
        "source_units": sum(len(r["units"]) for r in rows),
        "labelled_units": 0,
        "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "source_truth_read": False,
        "protected_evaluation_opened": False,
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "manifest.json", result)
    return {k: result[k] for k in ("status", "windows", "source_units", "labelled_units")}


# Require annotation spans to match the frozen source identities, boundaries and text.
def _validate_span_sources(original, row):
    nodes = {u["node_id"]: u for u in original["units"]}
    # Reject duplicate source node identity.
    if len(nodes) != len(original["units"]):
        raise ValueError("duplicate source node identity")
    order = {u["node_id"]: i for i, u in enumerate(original["units"])}
    units = row["units"]
    # Every reviewed span must refer to a known piece of source text.
    if any(u["node_id"] not in nodes for u in units):
        raise ValueError("reviewed span has unknown source node")
    # Reviewed spans must keep the original reading order.
    if units != sorted(units, key=lambda u: (order[u["node_id"]], u["text_span"]["start"])):
        raise ValueError("reviewed spans changed source sequence order")
    coverage = {nid: 0 for nid in nodes}
    for unit in units:
        source = nodes[unit["node_id"]]
        start, end = unit["text_span"]["start"], unit["text_span"]["end"]
        # Stop if the sequence spans have a gap, overlap or invalid extent.
        if (
            type(start) is not int
            or type(end) is not int
            or not coverage[unit["node_id"]] == start < end <= len(source["raw_text"])
        ):
            raise ValueError("sequence spans have a gap, overlap or invalid extent")
        # Stop if the sequence annotation changed source text or evidence.
        if (
            unit["raw_text"] != source["raw_text"][start:end]
            or unit["evidence"] != source["evidence"]
            or unit["physical_page"] != source["physical_page"]
        ):
            raise ValueError("sequence annotation changed source text or evidence")
        coverage[unit["node_id"]] = end
    # A sequence review must account for every source character, including unresolved text.
    if any(coverage[nid] != len(u["raw_text"]) for nid, u in nodes.items()):
        raise ValueError("sequence annotation omitted source characters")
    return nodes


# Bind human order review to frozen nodes, independent of reviewed span splits.
def source_order_digest(row: dict) -> str:
    """Bind human order review to frozen nodes, independent of reviewed span splits."""
    payload = {
        "source_pdf_checksum": row["source_pdf_checksum"],
        "units": [
            {k: u[k] for k in ("node_id", "physical_page", "raw_text", "evidence")}
            for u in row["units"]
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# Validate full span coverage, independent review and legal owner transitions.
def audit_sequence_reviews(template: Path, reviewed: Path) -> dict:
    """Validate full span coverage, independent review and legal owner transitions."""
    _reject_reserved_paths(template, reviewed)
    manifest = read_json(template / "manifest.json")
    verify_files(template, manifest["file_sha256"])
    source_rows = read_jsonl(template / "sequence_template.jsonl")
    originals = {r["dataset_page_id"]: r for r in source_rows}
    # Reject duplicate source sequence window.
    if len(originals) != len(source_rows):
        raise ValueError("duplicate source sequence window")
    for original in source_rows:
        # Training annotation templates must remain TUNING.
        if original["learning_partition"] != "TUNING":
            raise ValueError("training annotation templates must remain TUNING")
        if original.get("source_packet_path"):
            _reject_reserved_paths(original["source_packet_path"])
    rows = read_jsonl(reviewed)
    # Reject duplicate reviewed sequence window.
    if len({r["dataset_page_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate reviewed sequence window")
    labels, courses, groups = Counter(), set(), set()
    eligible_labels, window_results = Counter(), []
    eligible_windows, positive_windows, negative_windows, insufficient_windows = 0, 0, 0, 0
    complete, pending = 0, 0
    for row in rows:
        # Reject unknown reviewed sequence window.
        if row["dataset_page_id"] not in originals:
            raise ValueError("unknown reviewed sequence window")
        original = originals[row["dataset_page_id"]]
        for key in (
            "schema_version",
            "source_document_id",
            "source_pdf_checksum",
            "learning_partition",
            "learning_group",
            "central_physical_page",
            "source_packet_path",
            "context_physical_pages",
            "page_orders",
        ):
            # Stop if the sequence annotation changed frozen source identity or order.
            if row.get(key) != original.get(key):
                raise ValueError("sequence annotation changed frozen source identity or order")
        # Training annotations must remain TUNING.
        if row["learning_partition"] != "TUNING":
            raise ValueError("training annotations must remain TUNING")
        nodes = _validate_span_sources(original, row)
        if row["review_status"] == "PENDING":
            pending += 1
            window_results.append(
                {
                    "dataset_page_id": row["dataset_page_id"],
                    "training_eligible": False,
                    "reason": "PENDING_SOURCE_REVIEW" if nodes else "NO_EXTRACTED_SOURCE_UNITS",
                    "source_units": len(original["units"]),
                }
            )
            continue
        # Keep annotations explicitly pending or fully adjudicated; other states cannot be
        # used here.
        if row["review_status"] != "ADJUDICATED":
            raise ValueError("sequence annotations require PENDING or ADJUDICATED status")
        # Stop if the sequence window has no source units for adjudication.
        if not nodes:
            raise ValueError("sequence window has no source units for adjudication")
        reviewers = [row.get("reviewer"), row.get("adjudicator")]
        # Sequence gold requires two identified human source reviewers.
        if any(
            not isinstance(r, dict) or r.get("kind") != "HUMAN" or not r.get("name", "").strip()
            for r in reviewers
        ):
            raise ValueError("sequence gold requires two identified human source reviewers")
        # Sequence adjudication requires distinct source reviewers.
        if reviewers[0]["name"].strip().casefold() == reviewers[1]["name"].strip().casefold():
            raise ValueError("sequence adjudication requires distinct source reviewers")
        order_review = row.get("source_order_review") or {}
        # Sequence gold requires human source-order review bound to frozen units.
        if (
            order_review.get("status") != "CONFIRMED_AS_EXPORTED"
            or order_review.get("source_order_sha256") != source_order_digest(original)
            or order_review.get("reviewer") != reviewers[0]["name"]
            or order_review.get("adjudicator") != reviewers[1]["name"]
        ):
            raise ValueError(
                "sequence gold requires human source-order review bound to frozen units"
            )
        units = row["units"]
        states, last_owner, previous_boundary = {}, None, None
        closed_owners, window_labels = set(), Counter()
        for unit in units:
            start, end = unit["text_span"]["start"], unit["text_span"]["end"]
            role, owner, boundary, cue = (
                unit[k]
                for k in ("label", "owner_start_node_id", "boundary_after", "transition_cue")
            )
            # Stop if the sequence span lacks an approved label, boundary or cue.
            if role not in LABELS or boundary not in BOUNDARIES or cue not in CUES:
                raise ValueError("sequence span lacks an approved label, boundary or cue")
            # Uncertain boundaries must remain PENDING, not adjudicated gold.
            if boundary == "UNCERTAIN":
                raise ValueError("uncertain boundaries must remain PENDING, not adjudicated gold")
            # Course field requires a source-anchored course owner.
            if role in HEADER | CONTENT | {"CREDITS", "OTHER_METADATA"} and owner not in nodes:
                raise ValueError("course field requires a source-anchored course owner")
            # Noncourse/source-insufficient text cannot own a course.
            if (
                role in {"NONCOURSE", "SECTION_CONTEXT", "PAGE_FURNITURE", "SOURCE_INSUFFICIENT"}
                and owner is not None
            ):
                raise ValueError("noncourse/source-insufficient text cannot own a course")
            # Text marked as continuing the same course must keep the same course owner.
            if previous_boundary == "SAME_COURSE" and owner != last_owner:
                raise ValueError("SAME_COURSE transition changed source owner")
            # NEXT_COURSE transition requires a new anchored header.
            if previous_boundary == "NEXT_COURSE" and (
                owner is None or owner == last_owner or role not in HEADER
            ):
                raise ValueError("NEXT_COURSE transition requires a new anchored header")
            # EXIT_COURSE transition must leave course ownership.
            if previous_boundary == "EXIT_COURSE" and owner is not None:
                raise ValueError("EXIT_COURSE transition must leave course ownership")
            # Course transition requires a current source owner.
            if boundary in {"SAME_COURSE", "NEXT_COURSE", "EXIT_COURSE"} and not owner:
                raise ValueError("course transition requires a current source owner")
            # Outside-course transition cannot carry a course owner.
            if boundary == "OUTSIDE_COURSE" and owner:
                raise ValueError("outside-course transition cannot carry a course owner")
            if owner:
                # Course owner cannot reopen after its declared boundary.
                if owner in closed_owners:
                    raise ValueError("course owner cannot reopen after its declared boundary")
                # Course ownership must start at its anchored source header.
                if owner not in states and (unit["node_id"] != owner or role not in HEADER):
                    raise ValueError("course ownership must start at its anchored source header")
                observation = SpanObservation(
                    allocation_id=f"{unit['node_id']}:{start}:{end}",
                    proposed_role=role,
                    header_anchor=cue == "EXACT_HEADER",
                    credit_marker=cue == "PRINTED_CREDIT",
                    prerequisite_marker=cue == "PRINTED_PREREQUISITE",
                    narrative_restart=cue == "NARRATIVE_RESTART",
                )
                state, violation = transition(states.get(owner, SequenceState()), role, observation)
                # Reject a reviewed field sequence that violates the shared transition rules.
                if violation:
                    raise ValueError(f"sequence annotation violates {violation}")
                states[owner] = state
                courses.add((row["source_pdf_checksum"], owner))
                if boundary in {"EXIT_COURSE", "NEXT_COURSE"}:
                    closed_owners.add(owner)
            last_owner, previous_boundary = owner, boundary
            labels[role] += 1
            window_labels[role] += 1
        # Do not treat an unsupported open course ending as a resolved sequence boundary.
        if previous_boundary in {"SAME_COURSE", "NEXT_COURSE"}:
            raise ValueError("sequence ends with an unsupported dangling course boundary")
        insufficient = bool(window_labels["SOURCE_INSUFFICIENT"])
        insufficient_windows += insufficient
        if not insufficient:
            eligible_windows += 1
            eligible_labels.update(window_labels)
            positive_windows += bool(set(window_labels) & HEADER)
            negative_windows += bool(window_labels["NONCOURSE"])
        window_results.append(
            {
                "dataset_page_id": row["dataset_page_id"],
                "training_eligible": not insufficient,
                "reason": "SOURCE_INSUFFICIENT" if insufficient else "ADJUDICATED_SOURCE_SEQUENCE",
                "source_units": len(original["units"]),
                "labelled_spans": len(row["units"]),
            }
        )
        groups.add(row["learning_group"])
        complete += 1
    return {
        "audit_version": VERSION,
        "status": "ANNOTATIONS_PARTIAL"
        if pending or complete < len(originals)
        else "TUNING_ANNOTATIONS_COMPLETE",
        "adjudicated_windows": complete,
        "pending_windows": pending,
        "missing_windows": len(originals) - len(rows),
        "course_owners": len(courses),
        "institution_families": len(groups),
        "label_counts": dict(labels),
        "training_eligible_windows": eligible_windows,
        "training_eligible_label_counts": dict(eligible_labels),
        "positive_course_windows": positive_windows,
        "reviewed_noncourse_windows": negative_windows,
        "source_insufficient_windows": insufficient_windows,
        "windows_without_source_units": sum(not r["units"] for r in source_rows),
        "window_readiness": window_results,
        "readiness_blockers": [
            *(["NO_ADJUDICATED_COURSE_SEQUENCES"] if not positive_windows else []),
            *(["NO_ADJUDICATED_NONCOURSE_EXAMPLES"] if not negative_windows else []),
            "SOURCE_REVIEWED_TRANSITION_NEED_NOT_CERTIFIED",
            "INDEPENDENT_INSTITUTION_GROUPED_SEQUENCE_VALIDATION_NOT_PROVIDED",
        ],
        "crf_experiment_ready": False,
        "readiness_reason": (
            "This audit certifies annotation integrity only, never authorization to train. "
            "Need a reviewed transition-error rationale, positive/negative source sequences and "
            "independent institution-grouped sequence validation drawn from permitted material."
        ),
        "review_sha256": sha256(reviewed),
        "template_manifest_sha256": sha256(template / "manifest.json"),
    }
