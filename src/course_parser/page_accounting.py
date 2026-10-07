# Track every row and character of extracted PDF text, including text that belongs to no
# course or remains unresolved. Check that interpreting the page never loses, changes or
# duplicates its text.
"""Whole extracted-page retention; semantic dispositions remain parser proposals."""

from __future__ import annotations

import importlib
from collections import Counter, defaultdict

from .ownership import course_field_links

VERSION = "source-page-accounting-v1"
COURSE_ROLES = {
    "CODE",
    "TITLE",
    "DESCRIPTION",
    "CREDITS",
    "PREREQUISITES",
    "OTHER_METADATA",
    "SEPARATOR",
}
NONCOURSE_ROLES = {
    "REQUIREMENT_TEXT",
    "COURSE_REFERENCE",
    "SECTION_HEADER",
    "PAGE_FURNITURE",
    "TABLE",
    "UNRESOLVED",
}


# Index each original text piece and reject repeated or inconsistent source identities.
def _inventory(graph):
    # Build the original-text inventory first. Every later accounting entry must point back to
    # something in this inventory.
    nodes, pages = {}, {}
    for page in graph.pages:
        # Stop if the accounting source contains duplicate pages.
        if page.physical_page in pages:
            raise ValueError("accounting source contains duplicate pages")
        pages[page.physical_page] = page
        for node in page.nodes:
            # Stop if the accounting source contains duplicate document node IDs.
            if node.node_id in nodes:
                raise ValueError("accounting source contains duplicate document node IDs")
            # Stop if the accounting node evidence belongs to another source.
            if any(
                a.physical_page != page.physical_page
                or a.source_document_id != graph.identity.source_document_id
                or a.source_pdf_checksum != graph.identity.source_pdf_checksum
                for a in node.evidence
            ):
                raise ValueError("accounting node evidence belongs to another source")
            nodes[node.node_id] = (page.physical_page, node)
    return nodes, pages


# Build the full original-cell inventory against which interpreted layouts are checked.
def _source_inventory(graph, source_graph):
    nodes, pages = _inventory(graph)
    if source_graph is None:
        return nodes, pages
    original, original_pages = _inventory(source_graph)
    # Stop if the accounting graph lost, added or changed source identity.
    if (
        graph.identity.source_document_id != source_graph.identity.source_document_id
        or graph.identity.source_pdf_checksum != source_graph.identity.source_pdf_checksum
        or nodes.keys() != original.keys()
        or pages.keys() != original_pages.keys()
    ):
        raise ValueError("accounting graph lost, added or changed source identity")
    for nid, (number, node) in original.items():
        adopted_number, adopted = nodes[nid]
        # The accounting graph must preserve each original text piece and its source evidence.
        if (
            number != adopted_number
            or node.raw_text != adopted.raw_text
            or node.evidence != adopted.evidence
        ):
            raise ValueError("accounting graph changed native source cell evidence")
    return original, original_pages


# Split a cell into exact newline-preserving row spans.
def _line_spans(text):
    cursor = 0
    for line in text.splitlines(keepends=True) or [""]:
        yield cursor, cursor + len(line)
        cursor += len(line)


# Restrict source evidence to the exact text span being accounted for.
def _slice_evidence(node, start, end):
    if not node.raw_text:
        return [a.model_dump(mode="json") for a in node.evidence]
    parser = importlib.import_module("catalog_mining.course_extraction_v3.field_parser")
    return [
        a.model_dump(mode="json")
        for a in parser._slice_evidence(node.evidence, node.raw_text, start, end)
    ]


# Assign every extracted character to a course field, another page role or an unresolved span.
def build_page_accounting(
    graph, blocks, courses, *, source_graph=None, candidates=(), decisions=(), table_contexts=None
):
    """Partition every native source character, retaining unowned and empty cells."""
    original, pages = _source_inventory(graph, source_graph)
    adopted, _ = _inventory(graph)
    # Reject duplicate block identity in page accounting.
    if len({b.block_id for b in blocks}) != len(blocks):
        raise ValueError("duplicate block identity in page accounting")
    # One text block cannot belong to multiple course records without an explicitly supported
    # sharing rule.
    if len({c.block_id for c in courses}) != len(courses):
        raise ValueError("multiple course records own one block without a shared contract")
    # Use the checked course-to-field links as the starting point for assigning text
    # ownership.
    links = course_field_links(graph, blocks, courses)
    by_node = defaultdict(list)
    for link in links:
        by_node[link["node_id"]].append(link)
    proposed = defaultdict(set)
    headers = {c.candidate_id: c for c in candidates}
    for decision in decisions:
        if str(decision.status) == "COURSE_REFERENCE":
            header_nodes = headers[decision.candidate_id].node_ids
            requirement = "SOURCE_DISTRIBUTION_REQUIREMENT_LIST" in decision.reason_codes or any(
                (table_contexts or {}).get(nid, {}).get("container") == "REQUIREMENTS"
                for nid in header_nodes
            )
            for nid in header_nodes:
                proposed[nid].add("REQUIREMENT_TEXT" if requirement else "COURSE_REFERENCE")
    for nid, context in (table_contexts or {}).items():
        if context["container"] == "REQUIREMENTS":
            proposed[nid].add("REQUIREMENT_TEXT")
    # Blocks withheld by the field parser remain unresolved, never invented courses.
    unresolved_block_nodes = {
        nid for b in blocks if b.block_id not in {c.block_id for c in courses} for nid in b.node_ids
    }
    rows = []
    # Visit the original text inventory, including pieces that never became part of a course.
    for nid, (number, source) in original.items():
        node = adopted[nid][1]
        role = str(node.role)
        fallback = (
            "UNRESOLVED"
            if nid in unresolved_block_nodes
            else "PAGE_FURNITURE"
            if role == "PAGE_FURNITURE"
            else "SECTION_HEADER"
            if role == "SECTION_HEADING"
            else next(iter(proposed[nid]))
            if len(proposed[nid]) == 1
            else "UNRESOLVED"
            if len(proposed[nid]) > 1
            else "TABLE"
            if role in {"TABLE", "TABLE_ROW", "TABLE_CELL"}
            else "UNRESOLVED"
        )
        # Keep each row's original character positions and line endings so the ledger can be
        # checked against the PDF extraction.
        for index, (start, end) in enumerate(_line_spans(source.raw_text)):
            spans = []
            for link in sorted(by_node[nid], key=lambda x: x["text_span"]["start"]):
                left, right = (
                    max(start, link["text_span"]["start"]),
                    min(end, link["text_span"]["end"]),
                )
                if right <= left:
                    continue
                spans.append(
                    {
                        "text_span": {"start": left, "end": right},
                        "raw_text": source.raw_text[left:right],
                        "disposition": "COURSE_FIELD",
                        **{
                            k: link[k]
                            for k in (
                                "course_occurrence_id",
                                "block_id",
                                "allocation_id",
                                "role",
                                "record_fields",
                                "record_field_statuses",
                            )
                        },
                        "evidence": _slice_evidence(source, left, right),
                    }
                )
            if not by_node[nid]:
                spans = [
                    {
                        "text_span": {"start": start, "end": end},
                        "raw_text": source.raw_text[start:end],
                        "disposition": fallback,
                        "course_occurrence_id": None,
                        "block_id": None,
                        "allocation_id": None,
                        "role": None,
                        "record_fields": [],
                        "record_field_statuses": {},
                        "evidence": _slice_evidence(source, start, end),
                    }
                ]
            rows.append(
                {
                    "row_id": f"{nid}:source-line:{index}",
                    "node_id": nid,
                    "physical_page": number,
                    "text_span": {"start": start, "end": end},
                    "raw_text": source.raw_text[start:end],
                    "observed_node_role": str(source.role),
                    "interpreted_node_role": role,
                    "status": "MODEL_PROPOSAL",
                    "spans": spans,
                }
            )
    ledger = {
        "schema_version": VERSION,
        "source_document_id": graph.identity.source_document_id,
        "source_pdf_checksum": graph.identity.source_pdf_checksum,
        "scope": "ALL_EXTRACTED_NATIVE_CELLS_IN_EVERY_CONTEXT_PAGE",
        "rows": rows,
        "source_truth_read": False,
        "source_extraction_completeness_verified": False,
        "semantic_truth_assigned": False,
        "shared_ownership_allowed": False,
    }
    # Audit the completed ledger independently rather than assuming the assignments above
    # preserved all the text.
    ledger["audit"] = audit_page_accounting(
        graph, ledger, field_links=links, source_graph=source_graph
    )
    return ledger


# Check the ledger against the original text and reject missing, changed, duplicate or
# conflicting claims.
def audit_page_accounting(graph, ledger, *, field_links=(), source_graph=None):
    """Independently reject disappearing, changed, duplicated or multiply owned spans."""
    nodes, pages = _source_inventory(graph, source_graph)
    # Stop if the accounting identity or proposal-only scope changed.
    if (
        ledger["schema_version"] != VERSION
        or ledger["scope"] != "ALL_EXTRACTED_NATIVE_CELLS_IN_EVERY_CONTEXT_PAGE"
        or ledger["source_document_id"] != graph.identity.source_document_id
        or ledger["source_pdf_checksum"] != graph.identity.source_pdf_checksum
        or ledger["source_truth_read"] is not False
        or ledger["semantic_truth_assigned"] is not False
        or ledger["source_extraction_completeness_verified"] is not False
        or ledger["shared_ownership_allowed"] is not False
    ):
        raise ValueError("accounting identity or proposal-only scope changed")
    links = {x["allocation_id"]: x for x in field_links}
    # Reject duplicate allocation identity in accounting proof.
    if len(links) != len(field_links):
        raise ValueError("duplicate allocation identity in accounting proof")
    expected_rows = {
        f"{nid}:source-line:{i}": (nid, number, start, end, node)
        for nid, (number, node) in nodes.items()
        for i, (start, end) in enumerate(_line_spans(node.raw_text))
    }
    rows = ledger["rows"]
    # The ledger must contain exactly the rows present in the extracted source.
    if len(rows) != len(expected_rows) or {r["row_id"] for r in rows} != expected_rows.keys():
        raise ValueError("accounting lost, duplicated or introduced a source row")
    adopted, _ = _inventory(graph)
    owners, dispositions, characters, coverage = defaultdict(set), Counter(), Counter(), Counter()
    for row in rows:
        nid, number, start, end, node = expected_rows[row["row_id"]]
        # Stop if the accounting source row differs from native source.
        if (
            row["node_id"] != nid
            or row["physical_page"] != number
            or row["text_span"] != {"start": start, "end": end}
            or row["raw_text"] != node.raw_text[start:end]
            or row["observed_node_role"] != str(node.role)
            or row["interpreted_node_role"] != str(adopted[nid][1].role)
            or row["status"] != "MODEL_PROPOSAL"
        ):
            raise ValueError("accounting source row differs from native source")
        cursor = start
        # Every source row needs a recorded outcome, even when it is unresolved.
        if not row["spans"] or (not node.raw_text and len(row["spans"]) != 1):
            raise ValueError("accounting row has no disposition")
        for span in row["spans"]:
            left, right = span["text_span"]["start"], span["text_span"]["end"]
            # Stop if the accounting span changed source or left a gap/overlap.
            if (
                type(left) is not int
                or type(right) is not int
                or left != cursor
                or right > end
                or right < left
                or (right == left and node.raw_text)
                or span["raw_text"] != node.raw_text[left:right]
                or span["evidence"] != _slice_evidence(node, left, right)
            ):
                raise ValueError("accounting span changed source or left a gap/overlap")
            cursor = right
            disposition = span["disposition"]
            if disposition == "COURSE_FIELD":
                link = links.get(span["allocation_id"])
                # Stop if the accounting course span is not proven by its field allocation.
                if (
                    link is None
                    or link["node_id"] != nid
                    or link["physical_page"] != number
                    or left < link["text_span"]["start"]
                    or right > link["text_span"]["end"]
                    or span["role"] not in COURSE_ROLES
                    or any(
                        span[k] != link[k]
                        for k in (
                            "course_occurrence_id",
                            "block_id",
                            "role",
                            "record_fields",
                            "record_field_statuses",
                        )
                    )
                ):
                    raise ValueError("accounting course span is not proven by its field allocation")
                owners[nid].add(span["course_occurrence_id"])
                coverage[span["allocation_id"]] += right - left
            # Non-course text must use one of the supported ownership and outcome categories.
            elif (
                disposition not in NONCOURSE_ROLES
                or any(
                    span[k] is not None
                    for k in ("course_occurrence_id", "block_id", "allocation_id", "role")
                )
                or span["record_fields"]
                or span["record_field_statuses"]
            ):
                raise ValueError("accounting noncourse span has an unsupported owner/disposition")
            dispositions[disposition] += 1
            characters[number] += right - left
        # Account for the text at the end of the row as well as the earlier spans.
        if cursor != end:
            raise ValueError("accounting source row has an unaccounted tail")
    # A source row cannot belong to two different courses without an explicit sharing rule.
    if any(len(value) > 1 for value in owners.values()):
        raise ValueError("more than one course owns a native source row without a shared contract")
    # Retained owned spans cannot be relabeled as noncourse to hide a missing allocation.
    for link in field_links:
        covered = coverage[link["allocation_id"]]
        # The ledger must retain every checked course-field assignment with its original role.
        if covered != link["text_span"]["end"] - link["text_span"]["start"]:
            raise ValueError("accounting dropped or relabeled an owned field allocation")
    return {
        "status": "PASS",
        "source_cells": len(nodes),
        "source_rows": len(rows),
        "source_characters": sum(len(n.raw_text) for _, n in nodes.values()),
        "accounted_characters": sum(characters.values()),
        "disposition_span_counts": dict(dispositions),
        "unresolved_rows": sum(
            any(s["disposition"] == "UNRESOLVED" for s in r["spans"]) for r in rows
        ),
        "pages": [
            {
                "physical_page": number,
                "source_cells": len(page.nodes),
                "source_characters": sum(len(n.raw_text) for n in page.nodes),
                "accounted_characters": characters[number],
                "source_inventory_status": "NONEMPTY"
                if any(n.raw_text for n in page.nodes)
                else "EMPTY_SOURCE_INVENTORY",
            }
            for number, page in pages.items()
        ],
    }
