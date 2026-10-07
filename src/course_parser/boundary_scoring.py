# Explain why a course might stop or continue at a particular line. The scores help compare
# alternatives; they are not probabilities and cannot establish an ending on their own.
"""Explain endpoint evidence without turning weak layout cues into ownership truth.

Scores are provisional ranking weights, not probabilities or learned margins.
Related observations are capped by family so a header cannot count as several
independent votes. Missing geometry/typography remains explicitly unavailable.
"""

from __future__ import annotations

import re
from statistics import median

VERSION = "source-boundary-evidence-v1"
SCORE_KIND = "PROVISIONAL_UNCALIBRATED_NOT_A_PROBABILITY"
_PREREQUISITE = re.compile(r"^\s*(?:pre[- ]?requisites?|co[- ]?requisites?|prereqs?|pr\.)\b", re.I)
FAMILY_CAPS = {"structure": 8, "language": 3, "geometry": 2, "typography": 1, "field": 4}


# A proving method is not an endpoint; retain exact extent and excluded target.
def extent_key(block):
    """A proving method is not an endpoint; retain exact extent and excluded target."""
    return (
        tuple((s.node_id, s.physical_page, s.raw_text) for s in block.segments),
        tuple(block.physical_pages),
        str(block.continuation_status),
        tuple(sorted(block.boundary_node_ids)),
    )


# Collect the source clues supporting a possible course ending or continuation.
class BoundaryEvidence:
    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, graph, candidates, decisions, models, styles=None):
        from .contextual import PageFlow, box

        self.models, self.styles = models, styles or {}
        self.flows = {p.physical_page: PageFlow(p, models) for p in graph.pages}
        self.nodes = {n.node_id: n for p in graph.pages for n in p.nodes}
        self.starts = {c.code_slice.node_id: c for c in candidates}
        self.decisions = {d.candidate_id: d for d in decisions}
        self.last_page = graph.identity.total_physical_pages
        self.heights = {}
        for page in graph.pages:
            heights = [
                b[3] - b[1]
                for n in page.nodes
                if n.role != models.LayoutNodeRole.PAGE_FURNITURE
                and (b := box(n)) is not None
                and b[3] > b[1]
            ]
            self.heights[page.physical_page] = median(heights) if heights else None

    # Find the earliest available original text piece in verified reading order.
    def earliest(self, ids):
        return min(
            (self.nodes[nid] for nid in ids),
            key=lambda n: (
                n.evidence[0].physical_page,
                self.flows[n.evidence[0].physical_page].order.get(n.node_id, 10**9),
            ),
            default=None,
        )

    # Score a possible course boundary from independent source cues while preserving
    # continuation conflicts and review reasons.
    def score(self, last, target, *, action="STOP", scope="FULL_COURSE", kind=None):
        from .contextual import box

        sign = 1 if action == "STOP" else -1
        cues, unavailable, measurements = [], [], {}

        # Record one evidence cue together with its weight, family and supporting original
        # text pieces.
        def cue(code, family, value, nodes):
            cues.append(
                {
                    "code": code,
                    "family": family,
                    "weight": value,
                    "node_ids": [n.node_id for n in nodes if n is not None],
                }
            )

        if target is not None:
            entry = self.starts.get(target.node_id)
            decision = self.decisions.get(entry.candidate_id) if entry else None
            if decision and decision.status == self.models.EntryStatus.INDEPENDENT_COURSE:
                cue("NEXT_VERIFIED_COURSE_HEADER", "structure", 8 * sign, [target])
                if any("PATTERN" in r for r in decision.reason_codes):
                    cue("LOCAL_ENTRY_PATTERN_CORROBORATES_HEADER", "structure", sign, [target])
            # Internal metadata labels are never external course-end evidence.
            from .boundary_proposals import _INTERNAL

            if target.role == self.models.LayoutNodeRole.SECTION_HEADING and not (
                _INTERNAL.fullmatch(target.raw_text.strip()) or _PREREQUISITE.match(target.raw_text)
            ):
                cue("NEXT_EXTERNAL_SECTION", "structure", 8 * sign, [target])
            if _PREREQUISITE.match(target.raw_text):
                cue(
                    "PREREQUISITE_IS_FIELD_TRANSITION",
                    "field",
                    4 * sign if scope == "DESCRIPTION" else 0,
                    [target],
                )
        elif kind == self.models.BoundaryKind.DOCUMENT_END and (
            self.last_page == last.evidence[0].physical_page
        ):
            cue("KNOWN_DOCUMENT_END", "structure", 8 * sign, [last])
        if kind == self.models.BoundaryKind.TABLE_ROW_BOUNDARY:
            cue("TABLE_ROW_ENDPOINT", "structure", 8 * sign, [last])

        text = last.raw_text.rstrip()
        if re.search(r"[.!?][\]\)\"'’]*$", text):
            cue("TERMINAL_PUNCTUATION", "language", sign, [last])
        elif text:
            cue("POSSIBLY_UNFINISHED_SENTENCE", "language", -sign, [last])
        if target is not None:
            lp, tp = last.evidence[0].physical_page, target.evidence[0].physical_page
            cross_page = tp == lp + 1
            flow = self.flows[lp]
            same_column = tp == lp and flow.same_column(last.node_id, target.node_id)
            if cross_page and re.match(r"\s*[a-z]", target.raw_text):
                cue("LOWERCASE_ADJACENT_PAGE_CONTINUATION", "language", -2 * sign, [last, target])
            a, b = box(last), box(target)
            height = self.heights.get(lp)
            if a and b and height:
                if same_column:
                    gap = (b[1] - a[3]) / height
                    indent = abs(b[0] - a[0]) / height
                    measurements.update(
                        gap_in_page_line_heights=gap, indent_in_page_line_heights=indent
                    )
                    if gap >= 2:
                        cue("LARGE_RELATIVE_GAP", "geometry", sign, [last, target])
                    if indent <= 0.5 and not self.starts.get(target.node_id):
                        cue("ALIGNED_BODY_CONTINUES", "geometry", -sign, [last, target])
                elif tp == lp:
                    cue("COLUMN_CHANGE_IS_NOT_COURSE_END_PROOF", "geometry", 0, [last, target])
                else:
                    unavailable.append("CROSS_PAGE_INDENT_NOT_COMPARABLE_WITHOUT_COLUMN_ORIGINS")
            else:
                unavailable.append("RELATIVE_GEOMETRY_UNAVAILABLE")
            sa, sb = self.styles.get(last.node_id), self.styles.get(target.node_id)
            if sa and sb and sa.size > 0 and sb.size > 0:
                measurements["target_to_last_font_ratio"] = sb.size / sa.size
                if (
                    sa.bold != sb.bold
                    or sa.italic != sb.italic
                    or abs(sb.size / sa.size - 1) >= 0.15
                ):
                    cue("TYPOGRAPHY_CHANGE", "typography", sign, [last, target])
            else:
                unavailable.append("TYPOGRAPHY_UNAVAILABLE")
        # Group related clues before reporting the score so repeated evidence is not treated
        # as independent proof.
        families = {
            family: max(-cap, min(cap, sum(c["weight"] for c in cues if c["family"] == family)))
            for family, cap in FAMILY_CAPS.items()
        }
        return {
            "version": VERSION,
            "scope": scope,
            "score_kind": SCORE_KIND,
            "score": sum(families.values()),
            "family_scores": families,
            "cues": cues,
            "measurements": measurements,
            "unavailable": unavailable,
            "evidence": [
                a.model_dump(mode="json")
                for n in (last, target)
                if n is not None
                for a in n.evidence
            ],
        }

    # Weak internal stops remain questions; they never create truncated blocks.
    def review_alternatives(self, block):
        """Weak internal stops remain questions; they never create truncated blocks."""
        proposals = []
        for left_id, right_id in zip(block.node_ids, block.node_ids[1:], strict=False):
            left, right = self.nodes[left_id], self.nodes[right_id]
            score = self.score(left, right)
            codes = {c["code"] for c in score["cues"]}
            for code, scope in (
                ("PREREQUISITE_IS_FIELD_TRANSITION", "DESCRIPTION"),
                ("LARGE_RELATIVE_GAP", "FULL_COURSE"),
                ("COLUMN_CHANGE_IS_NOT_COURSE_END_PROOF", "FULL_COURSE"),
            ):
                if code not in codes:
                    continue
                proposals.append(
                    {
                        "origin": code,
                        "action": "STOP",
                        "scope": scope,
                        "status": "REVIEW_REQUIRED",
                        "target_node_id": right_id,
                        "proposed_end_node_id": left_id,
                        "violations": ["FIELD_TRANSITION_NOT_COURSE_ENDPOINT"]
                        if scope == "DESCRIPTION"
                        else ["LAYOUT_CUE_ALONE_CANNOT_PROVE_ENDPOINT"],
                        "ranking": self.score(left, right, scope=scope),
                        "evidence": score["evidence"],
                        "changes_owned_extent": False,
                    }
                )
        return proposals


# Deduplicate repeated proving origins before measuring the score margin.
def ranking_summary(proposals):
    """Deduplicate repeated proving origins before measuring the score margin."""
    groups = {}
    for index, p in enumerate(proposals):
        if p["status"] != "ADMISSIBLE" or p.get("scope", "FULL_COURSE") != "FULL_COURSE":
            continue
        key = p["extent_key"]
        group = groups.setdefault(key, {"proposal_indices": [], "score": p["ranking"]["score"]})
        group["proposal_indices"].append(index)
        group["score"] = max(group["score"], p["ranking"]["score"])
    ranked = sorted(groups.values(), key=lambda g: -g["score"])
    margin = ranked[0]["score"] - ranked[1]["score"] if len(ranked) > 1 else None
    return {
        "score_kind": SCORE_KIND,
        "admissible_endpoint_groups": ranked,
        "top_score_margin": margin,
        "decision": "ACCEPT_UNIQUE_VERIFIED_ENDPOINT" if len(ranked) == 1 else "REVIEW_REQUIRED",
        "conflict_policy": "DISTINCT_VERIFIED_EXTENTS_REQUIRE_REVIEW_PENDING_MARGIN_CALIBRATION",
        "excluded_from_acceptance": [
            i for i, p in enumerate(proposals) if p["status"] != "ADMISSIBLE"
        ],
    }
