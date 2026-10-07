# Look for older catalog headings that use a number without a subject prefix. These weaker
# patterns need supporting context and may remain review questions instead of course records.
"""Source-span cues for numeric headers, separate from modern subject codes.

The parenthesized pair is a structural delimiter only. Its academic meaning
requires a source legend; it is not automatically a credit value or a range.
Entry classification still requires bounded narrative and contextual/peer
evidence, and explicit negative containers retain their veto.
"""

import re

EXTENDED_NUMERIC = re.compile(r"^\s*(?P<code>\d{4,5})\.?\s+")
EXTENDED_SIGNAL = "HISTORICAL_EXTENDED_NUMERIC_HEADER_PROBE"
EXTENDED_REASON = "HISTORICAL_EXTENDED_IDENTIFIER_REQUIRES_LOCAL_PATTERN"

INLINE_NUMERIC_PAIR_TITLE = re.compile(
    r"(?P<title>[A-Z][A-Z0-9'’/&.,()-]*(?:\s+[A-Z][A-Z0-9'’/&.,()-]*)*)"
    r"\s+(?P<metadata>\(\s*\d{1,2}\s*[-–]\s*\d{1,2}\s*\))\s+(?=[A-Z][a-z])"
)

PAIR_SIGNAL = "HISTORICAL_INLINE_NUMERIC_PAIR_DELIMITER"


# Keep a source-supported weak header as a review/boundary candidate.
def extended_numeric_probes(detector, graph, pages, candidates, caps_title):
    """Keep a source-supported weak header as a review/boundary candidate.

    A long number plus a display title and ordinary inline prose is stronger
    than a number alone, but does not prove an independent entry. OCR may have
    joined a footnote to an identifier. Only the existing leave-one-out local
    source-pattern stage can resolve these candidates for automatic assembly.
    """
    import hashlib

    from .contextual import _historical_title_like

    m = detector.models
    seen = {c.code_slice.node_id for c in candidates}
    additions = []
    for page in graph.pages:
        if page.status != m.ResolutionStatus.CONFIRMED:
            continue
        flow = pages[page.physical_page]
        for node in page.nodes:
            match = EXTENDED_NUMERIC.match(node.raw_text)
            if node.node_id in seen or node.role != m.LayoutNodeRole.LINE or match is None:
                continue
            title = caps_title.match(node.raw_text, match.end())
            if not title or not _historical_title_like(title.group("title")):
                continue
            prose = node.raw_text[title.end() :]
            if len(re.findall(r"\b[^\W\d_]+\b", prose)) < 4:
                continue
            code_start, code_end = match.span("code")
            if node.raw_text[code_end : code_end + 1] == ".":
                code_end += 1
            candidate = m.HeaderCandidate(
                candidate_id="header-"
                + hashlib.sha256(
                    (graph.identity.source_document_id + ":" + node.node_id).encode()
                ).hexdigest()[:20],
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
                title_raw=title.group("title"),
                title_node_ids=(node.node_id,),
                title_slices=(
                    m.NodeTextSlice(
                        node_id=node.node_id,
                        text_span=m.TextSpan(start=title.start("title"), end=title.end("title")),
                        observed_text=title.group("title"),
                    ),
                ),
                structural_signals=(EXTENDED_SIGNAL,),
                contradictions=(EXTENDED_REASON,),
                evidence=node.evidence,
            )
            if flow.context(candidate)[0] not in {"REFERENCE", "NONCOURSE"}:
                additions.append(candidate)
    return [*candidates, *additions]
