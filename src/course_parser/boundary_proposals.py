# Compare the possible places where a course entry could end. Keep conflicting endings for
# review so one course does not quietly absorb text from the next.
"""Competing source boundary/continuation proposals with hard admission checks."""

from __future__ import annotations

import hashlib
import re

from .boundary_scoring import BoundaryEvidence, extent_key, ranking_summary

VERSION = "source-boundary-competition-v2"
_INTERNAL = re.compile(
    r"^(?:description|pre[- ]?requisites?|co[- ]?requisites?|credits?|units?|hours?|"
    r"offered|restrictions?|notes?)\s*[:.-]?\s*$",
    re.I,
)


# Check semantic and spatial transitions, beyond the base schema's types.
def boundary_violations(graph, block, candidates, decisions, owners, models):
    """Check semantic and spatial transitions, beyond the base schema's types."""
    from .contextual import PageFlow

    m = models
    flows = {p.physical_page: PageFlow(p, m) for p in graph.pages}
    nodes = {n.node_id: n for p in graph.pages for n in p.nodes}
    starts = {c.code_slice.node_id: c for c in candidates}
    statuses = {d.candidate_id: d.status for d in decisions}
    reasons = []
    for nid in block.node_ids:
        if owners.get(nid, block.candidate_id) != block.candidate_id:
            reasons.append("SOURCE_NODE_ALREADY_OWNED_BY_ANOTHER_COURSE")
        other = starts.get(nid)
        if other and other.candidate_id != block.candidate_id:
            status = statuses[other.candidate_id]
            if status != m.EntryStatus.COURSE_REFERENCE:
                reasons.append("CANNOT_CONTINUE_THROUGH_COMPETING_COURSE_OR_UNRESOLVED_HEADER")
    for hop in block.continuation_hops:
        source, target = nodes[hop.source_node_id], nodes[hop.target_node_id]
        if any(
            flows[p].page.status != m.ResolutionStatus.CONFIRMED
            for p in (hop.source_physical_page, hop.target_physical_page)
        ):
            reasons.append("CONTINUATION_REQUIRES_CONFIRMED_PAGE_ORDER")
        if target.node_id in starts:
            reasons.append("CONTINUATION_TARGET_CANNOT_BE_A_HEADER_CANDIDATE")
        if hop.kind == m.ContinuationKind.CROSS_COLUMN:
            flow = flows[hop.source_physical_page]
            if flow.next.get(source.node_id) != target.node_id:
                reasons.append("CROSS_COLUMN_REQUIRES_UNIQUE_CONFIRMED_EDGE")
        elif hop.target_physical_page != hop.source_physical_page + 1:
            reasons.append("CROSS_PAGE_REQUIRES_ADJACENT_PAGES")
    if block.boundary_status != m.BoundaryStatus.CONFIRMED:
        return tuple(sorted(set(reasons)))
    flow = flows[block.physical_pages[-1]]
    if flow.page.status != m.ResolutionStatus.CONFIRMED:
        reasons.append("CONFIRMED_ENDPOINT_REQUIRES_CONFIRMED_PAGE_ORDER")
    targets = [nodes[nid] for nid in block.boundary_node_ids]
    if block.boundary_kind == m.BoundaryKind.NEXT_INDEPENDENT_HEADER:
        if not any(
            n.node_id in starts
            and statuses[starts[n.node_id].candidate_id] == m.EntryStatus.INDEPENDENT_COURSE
            for n in targets
        ):
            reasons.append("STOP_REQUIRES_VERIFIED_INDEPENDENT_HEADER")
    elif block.boundary_kind == m.BoundaryKind.SECTION_BOUNDARY:
        if not targets or any(
            n.role != m.LayoutNodeRole.SECTION_HEADING or _INTERNAL.fullmatch(n.raw_text.strip())
            for n in targets
        ):
            reasons.append("STOP_REQUIRES_EXTERNAL_SOURCE_SECTION")
    elif block.boundary_kind == m.BoundaryKind.DOCUMENT_END:
        if graph.identity.total_physical_pages != block.physical_pages[-1]:
            reasons.append("DOCUMENT_END_REQUIRES_KNOWN_LAST_PHYSICAL_PAGE")
    elif block.boundary_kind == m.BoundaryKind.TABLE_ROW_BOUNDARY:
        if any(
            s.role not in {m.LayoutNodeRole.TABLE_CELL, m.LayoutNodeRole.TABLE_ROW}
            for s in block.segments
        ):
            reasons.append("ROW_ENDPOINT_REQUIRES_TABLE_ROW_MEMBERSHIP")
        return tuple(sorted(set(reasons)))
    if targets:
        # Header targets may include title fragments; test the earliest one.
        target = min(
            targets,
            key=lambda n: (
                n.evidence[0].physical_page,
                flows[n.evidence[0].physical_page].order.get(n.node_id, 10**9),
            ),
        )
        target_page = target.evidence[0].physical_page
        if target_page == flow.page.physical_page:
            if flow.next.get(block.end_node_id) != target.node_id:
                reasons.append("STOP_CANNOT_SKIP_UNOWNED_SOURCE_CONTENT")
        elif target_page == flow.page.physical_page + 1:
            neighbor = flows[target_page]
            meaningful = [
                n
                for n in neighbor.nodes.values()
                if n.raw_text.strip() and n.role != m.LayoutNodeRole.PAGE_FURNITURE
            ]
            first = (
                min(meaningful, key=lambda n: neighbor.order.get(n.node_id, 10**9))
                if meaningful
                else None
            )
            if neighbor.page.status != m.ResolutionStatus.CONFIRMED or first != target:
                reasons.append("ADJACENT_STOP_REQUIRES_FIRST_CONFIRMED_SOURCE_NODE")
            trailing = [
                n
                for n in flow.nodes.values()
                if flow.order.get(n.node_id, -1) > flow.order.get(block.end_node_id, -1)
                and n.role != m.LayoutNodeRole.PAGE_FURNITURE
            ]
            if trailing:
                reasons.append("ADJACENT_STOP_CANNOT_SKIP_TRAILING_SOURCE_CONTENT")
        else:
            reasons.append("STOP_CANNOT_SKIP_PHYSICAL_PAGES")
    return tuple(sorted(set(reasons)))


# A hard proven endpoint wins; conflicting extents keep ownership unresolved.
def select_verified(original, admitted, models):
    """A hard proven endpoint wins; conflicting extents keep ownership unresolved."""
    from .contextual import replace_validated

    m = models
    # Same source extent with distinct proving reasons is not a competing owner.
    distinct = {}
    for block in admitted:
        signature = extent_key(block)
        distinct.setdefault(signature, block)
    if len(distinct) == 1:
        return next(iter(distinct.values())), "UNIQUE_ADMISSIBLE_ENDPOINT"
    if not distinct and original.boundary_status != m.BoundaryStatus.CONFIRMED:
        return original, "RETAIN_UNRESOLVED_SOURCE_EXTENT"
    evidence = tuple(
        dict.fromkeys(
            [
                *original.unresolved_evidence,
                *original.boundary_evidence,
                *original.segments[-1].evidence,
            ]
        )
    )
    warning = m.ParserWarning(
        code="COMPETING_BOUNDARY_PROPOSALS" if distinct else "BOUNDARY_TRANSITION_REJECTED",
        stage="boundary_competition",
        message="No unique source-supported ownership endpoint passed the transition constraints.",
        severity=m.WarningSeverity.BLOCKING,
        evidence=evidence,
    )
    return replace_validated(
        original,
        boundary_status=m.BoundaryStatus.UNRESOLVED,
        boundary_kind=m.BoundaryKind.READING_ORDER_UNRESOLVED,
        boundary_reason_codes=(warning.code,),
        unresolved_evidence=evidence,
        warnings=tuple(dict.fromkeys([*original.warnings, warning])),
        builder_version=VERSION,
    ), "CONFLICTING_ENDPOINTS_WITHHELD" if distinct else "INVALID_ENDPOINT_WITHHELD"


# Compare real candidate blocks and explicit stop/continue alternatives.
def resolve_competing_boundaries(
    graph, candidates, decisions, original, structural, contextual, models, *, styles=None
):
    """Compare real candidate blocks and explicit stop/continue alternatives.

    Review-only continuation targets carry source evidence but cannot attach
    text. Every admitted candidate carries a complete validated CourseBlock.
    """
    from .contextual import PageFlow

    m = models
    flows = {p.physical_page: PageFlow(p, m) for p in graph.pages}
    nodes = {n.node_id: n for p in graph.pages for n in p.nodes}
    starts = {c.code_slice.node_id: c for c in candidates}
    statuses = {d.candidate_id: d.status for d in decisions}
    owners = {nid: b.candidate_id for b in original for nid in b.node_ids}
    scorer = BoundaryEvidence(graph, candidates, decisions, m, styles)
    result, reports = [], []
    # Compare the original, structural and contextual ending proposed for the same course.
    for before, base, refined in zip(original, structural, contextual, strict=True):
        proposed, admitted = [], []
        for origin, block in (
            ("SAME_PAGE_BLOCK", before),
            ("STRUCTURAL_CONTINUATION", base),
            ("SOURCE_ENDPOINT", refined),
        ):
            violations = boundary_violations(graph, block, candidates, decisions, owners, m)
            verified = block.boundary_status == m.BoundaryStatus.CONFIRMED and not violations
            if verified:
                admitted.append(block)
            proposed.append(
                {
                    "origin": origin,
                    "scope": "FULL_COURSE",
                    "action": "CONTINUE" if block.node_ids != before.node_ids else "STOP",
                    "status": "ADMISSIBLE"
                    if verified
                    else "REJECTED"
                    if violations
                    else "REVIEW_REQUIRED",
                    "owned_node_ids": list(block.node_ids),
                    "boundary_node_ids": list(block.boundary_node_ids),
                    "boundary_kind": str(block.boundary_kind),
                    "extent_key": hashlib.sha256(repr(extent_key(block)).encode()).hexdigest(),
                    # Score the final endpoint, including for a block which first continues
                    # across a column/page. Continuation action and endpoint action are
                    # distinct decisions.
                    "ranking": scorer.score(
                        nodes[block.end_node_id],
                        scorer.earliest(block.boundary_node_ids),
                        kind=block.boundary_kind,
                    ),
                    "reasons": list(block.boundary_reason_codes),
                    "violations": list(violations),
                    "evidence": [
                        a.model_dump(mode="json")
                        for a in (*block.boundary_evidence, *block.unresolved_evidence)
                    ],
                }
            )
        targets = list(before.boundary_node_ids)
        if not targets and before.boundary_kind == m.BoundaryKind.PAGE_END_UNRESOLVED:
            neighbor = flows.get(before.physical_pages[-1] + 1)
            if neighbor:
                targets = [
                    nid
                    for nid, n in neighbor.nodes.items()
                    if nid not in neighbor.previous and n.role != m.LayoutNodeRole.PAGE_FURNITURE
                ]
        for nid in targets:
            node = nodes[nid]
            entry = starts.get(nid)
            independent = entry and statuses[entry.candidate_id] == m.EntryStatus.INDEPENDENT_COURSE
            external_section = (
                node.role == m.LayoutNodeRole.SECTION_HEADING
                and not _INTERNAL.fullmatch(node.raw_text.strip())
            )
            proposed.append(
                {
                    "origin": "COMPETING_TARGET",
                    "scope": "FULL_COURSE",
                    "action": "CONTINUE",
                    "target_node_id": nid,
                    "ranking": scorer.score(nodes[before.end_node_id], node, action="CONTINUE"),
                    "status": "REJECTED"
                    if independent or external_section or entry
                    else "REVIEW_REQUIRED",
                    "violations": ["CANNOT_ATTACH_INDEPENDENT_HEADER_OR_SECTION"]
                    if independent or external_section
                    else ["HEADER_OWNERSHIP_REQUIRES_ADJUDICATION"]
                    if entry
                    else ["CONTINUATION_NOT_YET_PROVED"],
                    "evidence": [a.model_dump(mode="json") for a in node.evidence],
                }
            )
            if before.boundary_status != m.BoundaryStatus.CONFIRMED:
                proposed.append(
                    {
                        "origin": "COMPETING_TARGET",
                        "scope": "FULL_COURSE",
                        "action": "STOP",
                        "target_node_id": nid,
                        "ranking": scorer.score(nodes[before.end_node_id], node),
                        "status": "REVIEW_REQUIRED",
                        "violations": ["ENDPOINT_NOT_YET_PROVED"],
                        "evidence": [a.model_dump(mode="json") for a in node.evidence],
                    }
                )
        proposed.extend(scorer.review_alternatives(refined))
        chosen, reason = select_verified(before, admitted, m)
        # Include already selected continuations in the global ownership check.
        for nid in chosen.node_ids:
            owners[nid] = chosen.candidate_id
        result.append(chosen)
        reports.append(
            {
                "version": VERSION,
                "candidate_id": before.candidate_id,
                "block_id": before.block_id,
                "selection_reason": reason,
                "ranking": ranking_summary(proposed),
                "selected_node_ids": list(chosen.node_ids),
                "selected_boundary_status": str(chosen.boundary_status),
                "proposals": proposed,
            }
        )
    return tuple(result), reports
