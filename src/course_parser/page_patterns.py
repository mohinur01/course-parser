# Use nearby course headings as supporting evidence for weaker numeric headings. Compare
# distinct entries in the same local context rather than treating repeated text as independent
# proof.
"""Corroborate native numeric headers within one page, column and section.

This is source-local pattern inference, not training on review answers. A
candidate cannot validate itself: two other distinct identifiers must show the
same code shape, emphasis boundary and bounded narrative structure. Uncertain
tables, missing font runs and explicit negative containers abstain.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from collections import defaultdict
from statistics import median

_NUMERIC = re.compile(r"^\s*(?P<code>\d{1,5}(?:\.\d{1,5}[A-Z]?)?)[.):]?\s+")


# Corroborate a historical numeric candidate using other distinct source entries in the same
# local context.
def apply_local_numeric_patterns(detector, graph, pages, candidates):
    # Imported here to keep the contextual stage's public contracts in one place.
    from .contextual import (
        _ADMIN,
        _BARE_CREDIT,
        _DISTRIBUTION_REQUIREMENT,
        _PREREQ,
        LineStyle,
        _title_like,
        box,
        replace_validated,
    )

    m = detector.models
    fields = importlib.import_module("catalog_mining.course_extraction_v3.field_parser")
    header_patterns = importlib.import_module("catalog_mining.course_extraction_v3.header_patterns")
    by_node = {c.code_slice.node_id: c for c in candidates}
    groups = defaultdict(list)
    for page in graph.pages:
        if page.status != m.ResolutionStatus.CONFIRMED:
            continue
        flow = pages[page.physical_page]
        ordered = sorted(page.nodes, key=lambda n: flow.order.get(n.node_id, -1))
        for node in ordered:
            style = detector.styles.get(node.node_id, LineStyle())
            match = _NUMERIC.match(node.raw_text)
            end = style.bold_prefix_end
            b = box(node)
            if (
                match is None
                or node.role != m.LayoutNodeRole.LINE
                or not style.bold
                or end is None
                or not match.end() < end <= len(node.raw_text)
                or b is None
                or b[3] <= b[1]
            ):
                continue
            start = match.end()
            # A sentence delimiter may share the last bold credit run. It is still a
            # separator, with its original source offset preserved.
            if node.raw_text[:end].endswith(")."):
                end -= 1
            credit = fields._trailing_credit(node.raw_text, start, end)
            if credit is None:
                bare = _BARE_CREDIT.search(node.raw_text[start:end])
                credit = (start + bare.start(), start + bare.start(), end) if bare else None
            title_end = credit[0] if credit else end
            title = node.raw_text[start:title_end].strip()
            emphasized_credit_title = bool(
                credit
                and 1 <= len(title.split()) <= 16
                and not re.search(r"[.!?]$", title)
                and not (_ADMIN.match(title) or _PREREQ.search(title))
            )
            if not (_title_like(title) or emphasized_credit_title):
                continue
            prototype = by_node.get(node.node_id)
            if prototype is None:
                code_start, code_end = match.span("code")
                cid = (
                    "header-"
                    + hashlib.sha256(
                        (graph.identity.source_document_id + ":" + node.node_id).encode()
                    ).hexdigest()[:20]
                )
                prototype = m.HeaderCandidate(
                    candidate_id=cid,
                    physical_page=page.physical_page,
                    node_ids=(node.node_id,),
                    code_node_ids=(node.node_id,),
                    status=m.HeaderStatus.UNRESOLVED,
                    identifier_kind=m.HeaderIdentifierKind.HISTORICAL_NUMERIC,
                    arrangement=m.HeaderArrangement.INLINE,
                    code_raw=node.raw_text[code_start:code_end],
                    code_slice=m.NodeTextSlice(
                        node_id=node.node_id,
                        text_span=m.TextSpan(start=code_start, end=code_end),
                        observed_text=node.raw_text[code_start:code_end],
                    ),
                    evidence=node.evidence,
                )
            context = flow.context(prototype)[0]
            if prototype.status == m.HeaderStatus.REJECTED or context in {
                "REFERENCE",
                "NONCOURSE",
            }:
                continue
            # Never inherit a pattern across a structural section boundary.
            preceding = [
                n
                for n in ordered
                if flow.order.get(n.node_id, -1) < flow.order.get(node.node_id, -1)
                and n.role == m.LayoutNodeRole.SECTION_HEADING
                and (
                    flow.same_column(node.node_id, n.node_id)
                    or (box(n) and box(n)[0] <= b[0] < box(n)[2])
                )
            ]
            section = preceding[-1].node_id if preceding else None
            current, body = node.node_id, []
            inline = node.raw_text[end:].lstrip(" .:;")
            for _ in range(40):
                current = flow.next.get(current)
                if current is None or not flow.same_column(node.node_id, current):
                    break
                following = flow.nodes[current]
                if following.role != m.LayoutNodeRole.LINE or (
                    detector.styles.get(current, LineStyle()).bold
                    and (
                        _NUMERIC.match(following.raw_text)
                        or header_patterns.parse_header_lexeme(following.raw_text)
                    )
                ):
                    break
                body.append(following)
            texts = [inline, *[n.raw_text for n in body]]
            text = " ".join(
                t
                for t in texts
                if not (_ADMIN.match(t) or _PREREQ.match(t) or _DISTRIBUTION_REQUIREMENT.match(t))
            )
            # Source probes must contain an actual ordinary narrative, not just aligned codes,
            # credit values or a series of display headings.
            strong = not (
                len(re.findall(r"\b[^\W\d_]+\b", text)) < 4
                or not re.search(r"[.!?](?:\s|$)", text.lstrip(" .:;"))
                or (body and detector.styles.get(body[0].node_id, LineStyle()).bold)
            ) and (credit is not None or context == "INVENTORY")
            code = match.group("code")
            shape = tuple(
                len(part) for part in re.split(r"\.", code.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
            )
            key = (page.physical_page, flow.columns.get(node.node_id), section, shape)
            groups[key].append(
                {
                    "node": node,
                    "candidate": prototype,
                    "code": code,
                    "start": start,
                    "end": end,
                    "match": match,
                    "box": b,
                    "body": body,
                    "credit": credit,
                    "style": style,
                    "strong": strong,
                    "font_prefix_end": style.bold_prefix_end,
                }
            )

    updates, additions, proposals = {}, [], []
    for (page_number, column, section, shape), observations in groups.items():
        height = median(o["box"][3] - o["box"][1] for o in observations)
        # Distinct alignment bands within a column are separate patterns. The tolerance is
        # relative to observed line height, not PDF coordinates.
        clusters = []
        for observation in sorted(observations, key=lambda o: o["box"][0]):
            if not clusters or (observation["box"][0] - clusters[-1][0]["box"][0] > height * 2):
                clusters.append([])
            clusters[-1].append(observation)
        for cluster in clusters:
            codes = {o["code"] for o in cluster if o["strong"]}
            supported = len(codes) >= 3
            pattern_id = (
                "page-pattern-"
                + hashlib.sha256(
                    (
                        graph.identity.source_document_id
                        + ":"
                        + ":".join(sorted(o["node"].node_id for o in cluster))
                    ).encode()
                ).hexdigest()[:20]
            )
            body_indents = [
                (box(o["body"][0])[0] - o["box"][0]) / height
                for o in cluster
                if o["body"] and box(o["body"][0])
            ]
            font = median(o["style"].size for o in cluster)
            gaps = []
            for o in cluster:
                previous = pages[page_number].previous.get(o["node"].node_id)
                if previous and pages[page_number].same_column(o["node"].node_id, previous):
                    b = box(pages[page_number].nodes[previous])
                    if b:
                        gaps.append((o["box"][1] - b[3]) / height)
            page_font = median(
                [
                    detector.styles.get(n.node_id, LineStyle()).size
                    for n in pages[page_number].nodes.values()
                    if detector.styles.get(n.node_id, LineStyle()).size > 0
                ]
                or [0]
            )
            proposal = {
                "pattern_id": pattern_id,
                "source_document_id": graph.identity.source_document_id,
                "source_pdf_checksum": graph.identity.source_pdf_checksum,
                "physical_page": page_number,
                "column_component": "column-"
                + hashlib.sha256("\n".join(sorted(column)).encode()).hexdigest()[:16]
                if column
                else None,
                "section_node_id": section,
                "identifier_digit_lengths": list(shape),
                "title_placement": "INLINE_NATIVE_BOLD_PREFIX",
                "credit_placement": "TRAILING_PRINTED_CREDIT"
                if any(o["credit"] for o in cluster)
                else "NOT_OBSERVED",
                "median_description_indent_in_line_heights": median(body_indents)
                if body_indents
                else None,
                "header_font_relative_to_page_median": font / page_font if page_font else None,
                "median_entry_gap_in_line_heights": median(gaps) if gaps else None,
                "entry_gap_used_to_promote": False,
                "distinct_identifiers": len(codes),
                "status": "CORROBORATED_SOURCE_PATTERN" if supported else "ABSTAIN",
                "reason_code": "TWO_OTHER_DISTINCT_SOURCE_ENTRIES"
                if supported
                else "INSUFFICIENT_LOCAL_SOURCE_SUPPORT",
                "observations": [],
                "source_truth_read": False,
            }
            for o in cluster:
                peers = [
                    p["node"].node_id for p in cluster if p["strong"] and p["code"] != o["code"]
                ]
                proposal["observations"].append(
                    {
                        "node_id": o["node"].node_id,
                        "code_raw": o["code"],
                        "native_bold_prefix_end": o["font_prefix_end"],
                        "title_and_credit_end": o["end"],
                        "other_supporting_node_ids": peers,
                        "bounded_body_node_ids": [n.node_id for n in o["body"]],
                        "strong_source_probe": o["strong"],
                        "adopted": supported,
                    }
                )
                if not supported:
                    continue
                start, end = o["match"].span("code")
                # Match the existing header contract: a printed terminal period belongs to the
                # historical identifier. Never normalize it away while refining only its title
                # boundary.
                raw = o["node"].raw_text
                if raw[end : end + 1] == "." and raw[end + 1 : end + 2].isspace():
                    end += 1
                candidate = detector._header(
                    o["candidate"],
                    o["node"],
                    start,
                    end,
                    [(o["node"], o["start"], o["end"])],
                    historical=True,
                )
                reasons = tuple(
                    x for x in candidate.contradictions if not x.startswith("HISTORICAL_")
                )
                candidate = replace_validated(
                    candidate,
                    contradictions=reasons,
                    status=m.HeaderStatus.UNRESOLVED if reasons else m.HeaderStatus.SUPPORTED,
                    warnings=tuple(w for w in candidate.warnings if w.code in reasons),
                    structural_signals=tuple(
                        dict.fromkeys(
                            [
                                *candidate.structural_signals,
                                "LOCAL_PAGE_NUMERIC_PATTERN",
                            ]
                        )
                    ),
                )
                if o["node"].node_id in by_node:
                    updates[o["candidate"].candidate_id] = candidate
                else:
                    additions.append(candidate)
            proposals.append(proposal)
    return [updates.get(c.candidate_id, c) for c in candidates] + additions, proposals
