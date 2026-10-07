# Check whether a course entry carries on into another column or page. Keep uncertain
# connections for review rather than attaching nearby text just because it looks similar.
"""Assess transport transitions separately from field ownership and final extent.

Retain verified reference continuations. A bounded fallback uses source stream
terminals instead of fixed fractions of page height. Language/style scores are
diagnostics; acceptance additionally requires topology, unique ownership, native
geometry and a verified downstream endpoint. OCR windows remain separate.
"""

from __future__ import annotations

import importlib
import re

from .boundary_scoring import SCORE_KIND, BoundaryEvidence

VERSION = "source-continuation-assessment-v1"


# Return nonempty body text in reading order, leaving out repeated headers, footers and other
# page furniture.
def meaningful(topology, models):
    return [
        n
        for n in sorted(topology.by_id.values(), key=lambda n: topology.order.get(n.node_id, 10**9))
        if n.raw_text.strip() and n.role != models.LayoutNodeRole.PAGE_FURNITURE
    ]


# Return the possible starting text pieces for the page's reading streams; keep separate
# streams separate.
def entry_nodes(topology, module):
    # Each starting point may belong to a separate reading stream. Keep all of them; vertical
    # position alone does not tell us which stream comes first.
    return tuple(n for n in module._page_entry_nodes(topology) if n.raw_text.strip())


# Keep established continuation decisions and explain weaker alternatives without
# automatically attaching them.
class AssessedContinuationResolver:
    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, models, styles=None, *, allow_relative_attachment=False):
        self.models, self.styles = models, styles or {}
        # This fallback has not demonstrated an extraction gain on the exposed sample, so it
        # stays review-only unless explicitly enabled for an experiment.
        self.allow_relative_attachment = allow_relative_attachment
        self.module = importlib.import_module(
            "catalog_mining.course_extraction_v3.continuation_resolver"
        )
        self.base = self.module.StructuralContinuationResolver()
        self.reports = []

    # Work out where each course's text ends, keeping uncertain endings for review.
    def resolve(self, graph, profile, candidates, decisions, blocks):
        m, module = self.models, self.module
        baseline = self.base.resolve(graph, profile, candidates, decisions, blocks)
        pages = {p.physical_page: module._PageTopology(p) for p in graph.pages}
        starts = {}
        # Index possible headings first; text that may start a new course must not be borrowed
        # as a continuation.
        for candidate in candidates:
            for nid in candidate.code_node_ids:
                starts.setdefault(nid, []).append(candidate)
        starts = {nid: tuple(values) for nid, values in starts.items()}
        by_id = {d.candidate_id: d for d in decisions}
        owners = {nid: b.candidate_id for b in blocks for nid in b.node_ids}
        scorer = BoundaryEvidence(graph, candidates, decisions, m, self.styles)
        self.reports = []
        result = []
        # Start with the reference result for each course and explain any supported or
        # unresolved continuation.
        for original, adopted in zip(blocks, baseline, strict=True):
            # Keep continuations that the existing structural checks already support.
            if adopted.continuation_hops:
                hops = [self._hop_report(hop, scorer) for hop in adopted.continuation_hops]
                self.reports.append(
                    {
                        "version": VERSION,
                        "candidate_id": original.candidate_id,
                        "block_id": original.block_id,
                        "decision": "CONTINUE",
                        "method": "EXISTING_STRUCTURAL_PROOF",
                        "transitions": hops,
                        "reasons": list(adopted.continuation_reason_codes),
                    }
                )
            elif original.boundary_kind in {
                m.BoundaryKind.PAGE_END_UNRESOLVED,
                m.BoundaryKind.COLUMN_TRANSITION_UNRESOLVED,
            }:
                # For an unresolved ending, inspect the neighboring page and record why it
                # could or could not continue.
                replacement, report = self._relative_page(
                    graph, original, pages, starts, by_id, owners, scorer
                )
                self.reports.append(report)
                if replacement is not None:
                    adopted = replacement
            # Update text ownership after each course so a later course cannot claim the same
            # text.
            for nid in adopted.node_ids:
                owners[nid] = adopted.candidate_id
            result.append(adopted)
        return tuple(result)

    # Describe one proposed continuation hop and its source-supported acceptance or rejection.
    def _hop_report(self, hop, scorer):
        source, target = scorer.nodes[hop.source_node_id], scorer.nodes[hop.target_node_id]
        return {
            "source_node_id": source.node_id,
            "target_node_id": target.node_id,
            "source_physical_page": hop.source_physical_page,
            "target_physical_page": hop.target_physical_page,
            "kind": str(hop.kind),
            "ranking": scorer.score(source, target, action="CONTINUE"),
            "decision": "CONTINUE",
            "reasons": list(hop.reason_codes),
        }

    # Assess an adjacent-page continuation using relative source positions and ownership
    # checks.
    def _relative_page(self, graph, block, pages, starts, decisions, owners, scorer):
        from .contextual import box

        m, module = self.models, self.module
        page = pages[block.physical_pages[-1]]
        source = page.by_id[block.end_node_id]
        report = {
            "version": VERSION,
            "candidate_id": block.candidate_id,
            "block_id": block.block_id,
            "decision": "UNRESOLVED",
            "method": "RELATIVE_SOURCE_STREAM_PROPOSAL",
            "source_node_id": source.node_id,
            "source_physical_page": page.page.physical_page,
            "transitions": [],
            "reasons": [],
            "score_kind": SCORE_KIND,
        }
        if block.boundary_kind != m.BoundaryKind.PAGE_END_UNRESOLVED:
            report["reasons"] = ["COLUMN_CONTINUATION_NOT_PROVED_BY_REFERENCE_TOPOLOGY"]
            return None, report
        # A page continuation needs the immediately following physical page, not just the next
        # available cached page.
        neighbor = pages.get(page.page.physical_page + 1)
        if neighbor is None:
            report["reasons"] = ["ADJACENT_SOURCE_PAGE_UNAVAILABLE"]
            return None, report
        targets = entry_nodes(neighbor, module)
        for target in targets:
            report["transitions"].append(
                {
                    "source_node_id": source.node_id,
                    "target_node_id": target.node_id,
                    "source_physical_page": page.page.physical_page,
                    "target_physical_page": neighbor.page.physical_page,
                    "kind": str(m.ContinuationKind.CROSS_PAGE),
                    "ranking": scorer.score(source, target, action="CONTINUE"),
                    "decision": "UNRESOLVED",
                    "reasons": [],
                }
            )
        # Collect every reason attachment is unsafe before deciding whether this connection
        # can be used.
        violations = []
        if (
            page.page.status != m.ResolutionStatus.CONFIRMED
            or neighbor.page.status != m.ResolutionStatus.CONFIRMED
        ):
            violations.append("CONFIRMED_SOURCE_AND_TARGET_ORDER_REQUIRED")
        ordered = meaningful(page, m)
        if not ordered or ordered[-1].node_id != source.node_id:
            violations.append("SOURCE_MUST_BE_LAST_MEANINGFUL_PAGE_NODE")
        if len(targets) != 1:
            violations.append("UNIQUE_FIRST_SOURCE_STREAM_REQUIRED")
        if violations:
            report["reasons"] = violations
            return None, report
        target = targets[0]
        edge_targets = module._explicit_targets(graph, source.node_id)
        if edge_targets and set(edge_targets) != {target.node_id}:
            violations.append("EXPLICIT_TRANSITION_DISAGREES_WITH_UNIQUE_STREAM")
        # A possible new heading blocks attachment until its ownership has been resolved.
        if target.node_id in starts:
            # An unresolved header is a hard blocker, even if its text/style resembles
            # continuation. Its existence does not prove a STOP.
            violations.append("TARGET_HEADER_OWNERSHIP_MUST_BE_RESOLVED_BEFORE_ATTACHMENT")
        if not module._eligible_content(target):
            violations.append("TARGET_MUST_BE_BODY_CONTENT")
        if owners.get(target.node_id, block.candidate_id) != block.candidate_id:
            violations.append("TARGET_ALREADY_OWNED_BY_ANOTHER_COURSE")
        a, b = box(source), box(target)
        sh, th = (
            scorer.heights.get(page.page.physical_page),
            scorer.heights.get(neighbor.page.physical_page),
        )
        if not (a and b and sh and th):
            violations.append("NATIVE_RELATIVE_GEOMETRY_REQUIRED")
        else:
            # Measure indentation from each column's own left edge. A previous right column
            # can continue into the next page's left column. Measure the source text
            # indentation relative to the current column.
            def indent(node, topology, height):
                members = topology.columns.get(node.node_id)
                if not members and not module._single_text_band(topology):
                    return None
                local = (
                    [box(topology.by_id[nid]) for nid in members]
                    if members
                    else [box(n) for n in meaningful(topology, m)]
                )
                boxes = [b for b in local if b is not None]
                return (box(node)[0] - min(b[0] for b in boxes)) / height if boxes else None

            si, ti = indent(source, page, sh), indent(target, neighbor, th)
            report["relative_indentation"] = {"source": si, "target": ti}
            if si is None or ti is None or abs(si - ti) > 0.75:
                violations.append("COMPARABLE_COLUMN_RELATIVE_INDENT_REQUIRED")
            if not module._page_alignment_supported(source, target, page, neighbor):
                violations.append("ALIGNED_TEXT_STREAM_REQUIRED")
        sa, sb = self.styles.get(source.node_id), self.styles.get(target.node_id)
        if not sa or not sb or min(sa.size, sb.size) <= 0:
            violations.append("NATIVE_TYPOGRAPHY_REQUIRED_FOR_FALLBACK")
        elif not (
            0.8 <= sb.size / sa.size <= 1.25 and sa.bold == sb.bold and sa.italic == sb.italic
        ):
            violations.append("BODY_TYPOGRAPHY_MUST_AGREE")
        unfinished = not re.search(r"[.!?][\]\)\"'’]*\s*$", source.raw_text)
        lower = bool(re.match(r"\s*[a-z]", target.raw_text))
        if not (unfinished and lower):
            violations.append("CORROBORATING_UNFINISHED_AND_LOWERCASE_CONTINUATION_REQUIRED")
        extent = module._extend(target, neighbor, starts, decisions)
        if extent.boundary_status != m.BoundaryStatus.CONFIRMED:
            violations.append("DOWNSTREAM_ENDPOINT_MUST_BE_VERIFIED")
        if any(
            owners.get(n.node_id, block.candidate_id) != block.candidate_id for n in extent.nodes
        ):
            violations.append("CONTINUED_EXTENT_CANNOT_CROSS_EXISTING_OWNER")
        report["reasons"] = list(dict.fromkeys(violations))
        report["transitions"][0]["reasons"] = report["reasons"]
        if violations:
            return None, report
        reasons = (
            "LAST_MEANINGFUL_SOURCE_NODE",
            "UNIQUE_FIRST_ADJACENT_PAGE_STREAM",
            "COLUMN_RELATIVE_INDENT_AND_TYPOGRAPHY_AGREE",
            "UNFINISHED_LOWERCASE_CONTINUATION",
            "DOWNSTREAM_CONFIRMED_BOUNDARY",
        )
        transition = module._Transition(
            m.ContinuationKind.CROSS_PAGE, source, target, reasons, extent
        )
        adopted = module._apply(block, transition)
        # Check complete ownership/order contracts before allowing the fallback.
        from .boundary_proposals import boundary_violations

        flat_candidates = tuple(
            {c.candidate_id: c for values in starts.values() for c in values}.values()
        )
        errors = boundary_violations(
            graph, adopted, flat_candidates, tuple(decisions.values()), owners, m
        )
        if errors:
            report["reasons"] = list(errors)
            report["transitions"][0]["reasons"] = list(errors)
            return None, report
        report.update(decision="CONTINUE", reasons=list(reasons))
        report["transitions"][0].update(decision="CONTINUE", reasons=list(reasons))
        report["proposed_owned_node_ids"] = list(adopted.node_ids)
        report["proposed_physical_pages"] = list(adopted.physical_pages)
        if not self.allow_relative_attachment:
            report.update(
                decision="UNRESOLVED",
                proposed_decision="CONTINUE",
                review_status="PENDING_SOURCE_REVIEW",
                reasons=[*reasons, "RELATIVE_FALLBACK_REQUIRES_SOURCE_REVIEW"],
            )
            return None, report
        return adopted, report

    # Update the continuation report to reflect the final boundary decision, rather than
    # leaving a provisional result visible.
    def reconcile(self, blocks):
        """Expose the actual post-boundary decision, not a provisional assessment."""
        by_id = {b.candidate_id: b for b in blocks}
        for report in self.reports:
            final = by_id[report["candidate_id"]]
            report["assessment_decision"] = report["decision"]
            report["decision"] = (
                "CONTINUE"
                if final.continuation_hops
                else "STOP"
                if final.boundary_status == self.models.BoundaryStatus.CONFIRMED
                else "UNRESOLVED"
            )
            report["final_owned_node_ids"] = list(final.node_ids)
            report["final_physical_pages"] = list(final.physical_pages)
            report["final_boundary_status"] = str(final.boundary_status)
            report["final_continuation_status"] = str(final.continuation_status)
            report["final_boundary_reasons"] = list(final.boundary_reason_codes)


# Crop windows are representations, not physical course boundaries.
def assess_ocr_windows(left_frame, right_frame):
    """Crop windows are representations, not physical course boundaries.

    Input frames have already validated invertible transforms. This report only
    describes source-relative coverage; it never concatenates text or assigns
    course ownership. Even touching crops need source-backed sequence review.
    """
    same_pdf = left_frame.source_pdf_checksum == right_frame.source_pdf_checksum
    same_page = left_frame.physical_page == right_frame.physical_page
    a, b = left_frame.requested_upright_box, right_frame.requested_upright_box
    overlap = (
        max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
        if same_pdf and same_page
        else 0
    )
    reasons = ["OCR_WINDOWS_REQUIRE_SOURCE_SEQUENCE_REVIEW"]
    if not same_pdf:
        reasons = ["DIFFERENT_SOURCE_PDFS_CANNOT_JOIN"]
    elif same_page and overlap:
        reasons.append("OVERLAPPING_SOURCE_COVERAGE_MUST_NOT_DUPLICATE_TEXT")
    elif same_page:
        reasons.append("DISJOINT_CROP_COVERAGE_IS_NOT_READING_ORDER_PROOF")
    elif abs(left_frame.physical_page - right_frame.physical_page) != 1:
        reasons.append("NONADJACENT_PHYSICAL_PAGES_CANNOT_JOIN")
    return {
        "version": VERSION,
        "kind": "OCR_WINDOW",
        "decision": "UNRESOLVED",
        "reasons": reasons,
        "same_source_pdf": same_pdf,
        "same_physical_page": same_page,
        "source_overlap_area_points2": overlap,
        "frames": [f.model_dump(mode="json") for f in (left_frame, right_frame)],
        "text_concatenated": False,
        "course_ownership_assigned": False,
    }
