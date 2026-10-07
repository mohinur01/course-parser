# Decide which extracted text belongs to each accepted course entry. Prevent two courses from
# claiming the same text, and keep unresolved boundaries visible.
"""Constrained course ownership over existing, source-anchored v3 blocks."""

from __future__ import annotations

import importlib
import re

from .contextual import PageFlow, box, replace_validated

_HEADER_METADATA = re.compile(r"(?:\d+(?:\.\d+)?\s*|(?:cr|hrs?|credits?|units?)\.?\s*)+", re.I)


# Build the text block for each accepted course while respecting disputed boundaries and
# metadata.
class OwnedCourseBlockBuilder:
    """Recover metadata between code and title, never cross a disputed boundary.

    The reference builder starts its traversal after the last header node. That
    omits same-row credits when the title is below the code. Only a connected,
    same-column path containing numeric/unit fragments can fill that gap.
    """

    version = "source-owned-course-blocks-v2"

    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, models):
        self.models = models
        self.module = importlib.import_module("catalog_mining.course_extraction_v3.block_builder")
        self.base = self.module.StructuralCourseBlockBuilder()

    # Assemble course ownership blocks from accepted course entries while retaining rejected
    # or unresolved source relationships.
    def build(self, graph, profile, candidates, decisions):
        blocks = self.base.build(graph, profile, candidates, decisions)
        headers = {c.candidate_id: c for c in candidates}
        flows = {p.physical_page: PageFlow(p, self.models) for p in graph.pages}
        starts = {c.code_slice.node_id for c in candidates}
        result = []
        for block in blocks:
            c = headers[block.candidate_id]
            flow = flows[c.physical_page]
            first = c.code_slice.node_id
            last = max(c.node_ids, key=lambda nid: flow.order.get(nid, -1))
            current, missing, visited = first, [], set()
            # Walk from the code to the final heading fragment, collecting intervening
            # metadata only within the same column.
            while current != last:
                # Stop a metadata walk that loops back to previously visited text.
                if current in visited:
                    raise ValueError("header metadata traversal encountered a reading-order cycle")
                visited.add(current)
                following = flow.next.get(current)
                if not following or not flow.same_column(first, following):
                    missing = []
                    break
                n = flow.nodes[following]
                if following not in c.node_ids:
                    if following in starts or not _HEADER_METADATA.fullmatch(n.raw_text.strip()):
                        missing = []
                        break
                    a, b = box(flow.nodes[first]), box(n)
                    if not a or not b or min(a[3], b[3]) <= max(a[1], b[1]):
                        missing = []
                        break
                    missing.append(n)
                current = following
            if missing:
                owned = sorted(
                    [*(flow.nodes[nid] for nid in block.node_ids), *missing],
                    key=lambda n: flow.order[n.node_id],
                )
                segments = tuple(
                    self.models.BlockSegment(
                        node_id=n.node_id,
                        physical_page=c.physical_page,
                        role=n.role,
                        raw_text=n.raw_text,
                        evidence=n.evidence,
                    )
                    for n in owned
                )
                raw = "\n".join(n.raw_text for n in owned)
                normalized = importlib.import_module(
                    "catalog_mining.course_extraction_v3.normalization"
                ).normalize_text(raw)
                block = replace_validated(
                    block,
                    segments=segments,
                    node_ids=tuple(n.node_id for n in owned),
                    raw_text=raw,
                    normalized_text=normalized.value,
                    normalization_steps=normalized.steps,
                    evidence=self.module._deduplicate_evidence(
                        a for n in owned for a in n.evidence
                    ),
                    builder_version=self.version,
                )
            result.append(block)
        audit_owned_blocks(graph, result)
        return tuple(result)


# Reject double ownership or source mutations; uncertainty is not a negative.
def audit_owned_blocks(graph, blocks):
    """Reject double ownership or source mutations; uncertainty is not a negative."""
    nodes = {n.node_id: n for p in graph.pages for n in p.nodes}
    owners = {}
    for block in blocks:
        for segment in block.segments:
            node = nodes[segment.node_id]
            # Stop if the owned segment differs from its source node.
            if segment.raw_text != node.raw_text or segment.evidence != node.evidence:
                raise ValueError("owned segment differs from its source node")
            # Stop if the source node is owned by more than one course.
            if segment.node_id in owners:
                raise ValueError("source node is owned by more than one course")
            owners[segment.node_id] = block.candidate_id
    return {"status": "PASS", "owned_nodes": len(owners), "courses": len(blocks)}


# Export checked source-span → existing course/field relationships.
def course_field_links(graph, blocks, courses):
    """Export checked source-span → existing course/field relationships.

    These are parser proposals, not source-reviewed labels. Keep separators and
    unallocated text too, so missing ownership cannot disappear in this export.
    A record field can be withheld even when its source allocation is present.
    """
    audit_owned_blocks(graph, blocks)
    nodes = {n.node_id: n for p in graph.pages for n in p.nodes}
    by_block = {b.block_id: b for b in blocks}
    field_parser = importlib.import_module("catalog_mining.course_extraction_v3.field_parser")
    links, seen_courses = [], set()
    fields = ("code", "title", "description", "credits", "prerequisites")
    for course in courses:
        # Reject duplicate course identity in field links.
        if course.course_occurrence_id in seen_courses:
            raise ValueError("duplicate course identity in field links")
        seen_courses.add(course.course_occurrence_id)
        block = by_block[course.block_id]
        # Stop if the field links do not belong to this source/course block.
        if (
            course.header_candidate_id != block.candidate_id
            or course.source.source_document_id != graph.identity.source_document_id
            or course.source.source_pdf_checksum != graph.identity.source_pdf_checksum
        ):
            raise ValueError("field links do not belong to this source/course block")
        allocations = {}
        for allocation in course.field_allocations:
            # Stop if the field link is outside its owned course block.
            if allocation.node_id not in block.node_ids:
                raise ValueError("field link is outside its owned course block")
            node = nodes[allocation.node_id]
            # Stop if the field link differs from its native source span.
            if (
                allocation.segment_raw_text != node.raw_text
                or allocation.evidence
                != field_parser._slice_evidence(
                    node.evidence,
                    node.raw_text,
                    allocation.text_span.start,
                    allocation.text_span.end,
                )
                or allocation.raw_text
                != node.raw_text[allocation.text_span.start : allocation.text_span.end]
            ):
                raise ValueError("field link differs from its native source span")
            allocations.setdefault(allocation.node_id, []).append(allocation)
            membership = [
                name
                for name in fields
                if allocation.allocation_id in getattr(course, name).allocation_ids
            ]
            links.append(
                {
                    "status": "MODEL_PROPOSAL",
                    "source_document_id": graph.identity.source_document_id,
                    "source_pdf_checksum": graph.identity.source_pdf_checksum,
                    "course_occurrence_id": course.course_occurrence_id,
                    "header_candidate_id": course.header_candidate_id,
                    "block_id": course.block_id,
                    "allocation_id": allocation.allocation_id,
                    "node_id": allocation.node_id,
                    "physical_page": allocation.physical_page,
                    "text_span": allocation.text_span.model_dump(mode="json"),
                    "raw_text": allocation.raw_text,
                    "role": str(allocation.kind),
                    "record_fields": membership,
                    "record_field_statuses": {
                        name: str(getattr(course, name).status) for name in membership
                    },
                    "evidence": [a.model_dump(mode="json") for a in allocation.evidence],
                }
            )
        for segment in block.segments:
            cursor = 0
            for allocation in sorted(
                allocations.get(segment.node_id, []), key=lambda a: a.text_span.start
            ):
                # Stop if the field links overlap or leave an unaccounted source gap.
                if allocation.text_span.start != cursor:
                    raise ValueError("field links overlap or leave an unaccounted source gap")
                cursor = allocation.text_span.end
            # Stop if the field links do not conserve the complete source segment.
            if cursor != len(segment.raw_text):
                raise ValueError("field links do not conserve the complete source segment")
    return links
