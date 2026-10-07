# Place existing course proposals and possible omissions into review categories. Use only
# source evidence, preserve every candidate and require human review before publication.
"""Source-only proposal routing. Bands are evidence categories, never probabilities."""

from __future__ import annotations

from collections import Counter

VERSION = "course-parser-precision-routing-v1"
SUPPORTED = {"OBSERVED_EXACT", "SAFE_NORMALIZED"}
RESOLVED_CONTINUATION = {
    "NONE",
    "CONFIRMED_SAME_REGION",
    "CONFIRMED_CROSS_COLUMN",
    "CONFIRMED_CROSS_PAGE",
}


# Build a lookup by identifier and reject duplicate identities.
def _index(rows, key):
    result = {x[key]: x for x in rows}
    # Reject duplicate precision input identity.
    if len(result) != len(rows):
        raise ValueError(f"duplicate precision input identity: {key}")
    return result


# Assign every existing candidate a review category using its source evidence and current
# parser result.
def route_window(
    metadata: dict, proposal: dict, records: list[dict], accounting: dict
) -> list[dict]:
    """Route existing proposals without changing detection, ownership, text or publication.

    The caller must first validate typed stages and native whole-page accounting.
    Missing optional metadata cannot turn a creditless course into a false course.
    """
    window = metadata["dataset_page_id"]
    # Routing must use predictions tied to this page selection and generated without review
    # answers.
    if proposal["dataset_page_id"] != window or proposal["source_truth_read"] is not False:
        raise ValueError("precision routing requires a bound source-only window")
    if proposal["status"] == "PARSER_STAGE_FAILED":
        # A failed parser run must not also claim to have produced course records.
        if records:
            raise ValueError("failed parser window cannot emit precision courses")
        return []  # The separate window-failure queue retains this condition.
    # Verify that all extracted text is accounted for before routing any course proposals.
    if proposal["status"] != "PROPOSED" or accounting.get("audit", {}).get("status") != "PASS":
        raise ValueError("precision routing requires passing native accounting")
    # Connect headings, decisions, text blocks and records by their IDs before assigning
    # review categories.
    candidates = _index(proposal["candidates"], "candidate_id")
    decisions = _index(proposal["decisions"], "candidate_id")
    blocks = _index(proposal["blocks"], "block_id")
    courses = _index([x["course"] for x in records], "header_candidate_id")
    _index([x["course"] for x in records], "course_occurrence_id")
    # Every heading needs a matching decision, and every course record must refer to one of
    # those headings.
    if candidates.keys() != decisions.keys() or not courses.keys() <= candidates.keys():
        raise ValueError("precision candidates, decisions and records do not match")
    for r in records:
        course = r["course"]
        c = candidates[course["header_candidate_id"]]
        # Keep each record tied to its source page and review status; routing cannot approve
        # it for publication.
        if (
            r["dataset_page_id"] != window
            or r["header_physical_page"] != c["physical_page"]
            or r["scope"]
            != ("CENTRAL" if c["physical_page"] == metadata["central_physical_page"] else "CONTEXT")
            or course["source"]["source_pdf_checksum"] != metadata["source_pdf_checksum"]
            or course["publication_status"] == "AUTO_ACCEPTED"
        ):
            raise ValueError("precision record source/scope/publication contract differs")
    result = []
    # Give every candidate a route, including candidates that never became course records.
    for cid, candidate in candidates.items():
        decision = decisions[cid]
        course = courses.get(cid)
        page = candidate["physical_page"]
        # A candidate may use evidence only from its recorded source PDF and selected pages.
        if page not in metadata["context_physical_pages"] or any(
            e["source_pdf_checksum"] != metadata["source_pdf_checksum"]
            or e["physical_page"] not in metadata["context_physical_pages"]
            for e in candidate["evidence"]
        ):
            raise ValueError("precision candidate evidence belongs to another source")
        reasons, metadata_reasons = [], []
        # References and non-course text belong in a separate audit queue, not in the course-
        # proposal queue.
        if decision["status"] in {"COURSE_REFERENCE", "NONCOURSE"}:
            # A reference or non-course decision must not also have an extracted course
            # record.
            if course:
                raise ValueError("negative entry decision cannot own a course")
            band, action = "EXCLUDED", "REFERENCE_OR_NONCOURSE_AUDIT"
            reasons.append(decision["status"])
        # An existing course record can be proposed, but its unresolved evidence may still
        # require additional review.
        elif course:
            # An extracted record needs a decision confirming that this is an independent
            # course entry.
            if decision["status"] != "INDEPENDENT_COURSE":
                raise ValueError("course requires an independent entry decision")
            block = blocks.get(course["block_id"])
            # The text block must belong to the same candidate as the course record.
            if block is None or block["candidate_id"] != cid:
                raise ValueError("precision course block identity differs")
            if candidate["status"] != "SUPPORTED" or not candidate["evidence"]:
                reasons.append("HEADER_SUPPORT_UNRESOLVED")
            if (
                decision["context_status"] != "CONFIRMED"
                or not decision["supporting_node_ids"]
                or not decision["evidence"]
            ):
                reasons.append("ENTRY_CONTEXT_UNRESOLVED")
            if (
                candidate["contradictions"]
                or decision["contradictions"]
                or decision["contradicting_node_ids"]
                or decision["context_kind"] not in {"COURSE_INVENTORY", "LOCAL_COURSE_DETAILS"}
            ):
                reasons.append("CONTRADICTING_ENTRY_EVIDENCE")
            if block["unresolved_evidence"]:
                reasons.append("UNRESOLVED_BLOCK_EVIDENCE")
            if any(x["boundary_status"] != "CONFIRMED_BOUNDARY" for x in (course, block)):
                reasons.append("BOUNDARY_UNRESOLVED")
            if any(x["continuation_status"] not in RESOLVED_CONTINUATION for x in (course, block)):
                reasons.append("CONTINUATION_UNRESOLVED")
            # The code and title need their own supporting source text; a record's existence
            # alone is not enough.
            for name in ("code", "title"):
                field = course[name]
                if field["status"] not in SUPPORTED or not field["raw"] or not field["evidence"]:
                    reasons.append(f"{name.upper()}_UNRESOLVED")
            # Distinguish a supported complete description from an explicitly absent
            # description and an unresolved one.
            description = course["description"]
            complete = (
                course["description_status"] == "COMPLETE_DESCRIPTION"
                and description["status"] in SUPPORTED
                and bool(description["raw"])
                and bool(description["evidence"])
            )
            absent = (
                course["description_status"] == "SOURCE_HAS_NO_DESCRIPTION"
                and description["status"] == "ABSENT_IN_SOURCE"
                and description["raw"] is None
                and bool(description["evidence"])
            )
            if not (complete or absent):
                reasons.append("DESCRIPTION_UNRESOLVED")
            if course["warnings"] or block["warnings"] or candidate["warnings"]:
                reasons.append("PARSER_WARNING_REQUIRES_REVIEW")
            if course["publication_status"] == "UNSUPPORTED":
                reasons.append("PUBLICATION_UNSUPPORTED")
            for name in ("credits", "prerequisites"):
                if course[name]["status"] not in SUPPORTED | {"ABSENT_IN_SOURCE"}:
                    metadata_reasons.append(f"{name.upper()}_{course[name]['status']}")
                if course[name]["status"] in {"PARTIAL", "AMBIGUOUS", "WITHHELD"}:
                    reasons.append(f"{name.upper()}_REQUIRES_REVIEW")
            band = "MEDIUM" if reasons else "HIGH"
            action = "PROPOSE_WITH_REVIEW" if reasons else "AUTO_PROPOSAL_ELIGIBLE"
            if not reasons:
                reasons.append("SOURCE_SUPPORTED_RESOLVED_CORE_PROPOSAL")
        elif decision["status"] == "INDEPENDENT_COURSE":
            band, action = "MEDIUM", "SOURCE_REVIEW_ONLY"
            reasons.append("INDEPENDENT_ENTRY_WITHOUT_RECOVERED_RECORD")
        elif decision["status"] == "UNRESOLVED":
            band, action = "LOW", "OMISSION_QUESTION_ONLY"
            reasons.append("INDEPENDENT_ENTRY_NOT_ESTABLISHED")
        else:
            raise ValueError("unknown precision entry decision")
        result.append(
            {
                "schema_version": VERSION,
                "dataset_page_id": window,
                "source_pdf_checksum": metadata["source_pdf_checksum"],
                "candidate_id": cid,
                "physical_page": page,
                "scope": "CENTRAL" if page == metadata["central_physical_page"] else "CONTEXT",
                "node_ids": candidate["node_ids"],
                "evidence": candidate["evidence"],
                "band": band,
                "action": action,
                "reason_codes": reasons,
                "metadata_review_reasons": metadata_reasons,
                "course_occurrence_id": course["course_occurrence_id"] if course else None,
                "existing_record_retained": course is not None,
                "human_review_required": True,
                "publication_authorized": False,
                "source_truth_read": False,
                "calibrated_probability": None,
                "review_decision": None,
            }
        )
    return result


# Verify that routing kept all candidates and did not change their source text, decisions or
# publication status.
def audit_routes(metadata, proposal, records, accounting, routes):
    """Reject omitted routes, promotion, filled answers and source/identity mutation."""
    # The saved routes must match the fixed review policy applied to source-only evidence.
    if routes != route_window(metadata, proposal, records, accounting):
        raise ValueError("precision routes differ from the source-only policy")
    return {
        "status": "PASS",
        "routes": len(routes),
        "bands": dict(Counter(r["band"] for r in routes)),
    }


# Write the separate proposal, review, omission and exclusion queues without approving
# publication.
def write_route_outputs(output, routes, failures):
    from .io import write_jsonl

    write_jsonl(output / "precision_routes.jsonl", routes)
    write_jsonl(
        output / "precision_medium_review.jsonl", [r for r in routes if r["band"] == "MEDIUM"]
    )
    write_jsonl(
        output / "precision_omission_questions.jsonl", [r for r in routes if r["band"] == "LOW"]
    )
    write_jsonl(output / "precision_window_failures.jsonl", failures)
