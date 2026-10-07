# Tests for course text crossing pages, columns and OCR crops without unsupported attachment.
"""Transport decisions, hard ownership blockers and relative-margin experiments."""

import importlib

import pytest

from course_parser.contextual import ContextualContinuationResolver, LineStyle
from course_parser.continuation import AssessedContinuationResolver, assess_ocr_windows


# Build a small test input with the source identity and values required by this test group.
def scenario(reference, *, source_y=300, ambiguous=False, header_only=False, scale=1):
    m = reference.models

    # Create a original text piece with exact text, page coordinates and evidence links.
    def node(nid, text, physical_page, y, role=None):
        anchor = m.EvidenceAnchor(
            source_document_id="continuation-fixture",
            source_pdf_checksum="8" * 64,
            physical_page=physical_page,
            node_id=nid,
            representation_id=f"page-{physical_page}",
            observed_text=text,
            extraction_method="PYMUPDF_EMBEDDED_TEXT",
            geometry=m.BoundingBox(
                x0=40 * scale,
                x1=270 * scale,
                y0=y * scale,
                y1=(y + 14) * scale,
                coordinate_system="PDF_POINTS_CANONICAL",
            ),
        )
        return m.GraphNode(
            node_id=nid,
            raw_text=text,
            evidence=(anchor,),
            engine="SYNTHETIC",
            role=role or m.LayoutNodeRole.LINE,
        )

    # Build a synthetic reading-order link between the requested original text pieces.
    def edge(a, b, kind):
        return m.GraphEdge(
            source_node_id=a,
            target_node_id=b,
            kind=kind,
            status=m.ResolutionStatus.CONFIRMED,
            reason="verified fixture order",
        )

    h1 = node("h1", "GEOL 451 Mineral Deposits", 1, source_y - 30)
    d1 = node("d1", "Begins study of ore deposits", 1, source_y)
    f2 = node("f2", "COURSE CATALOG", 2, 5, m.LayoutNodeRole.PAGE_FURNITURE)
    d2 = node("d2", "and continues with field methods.", 2, 45)
    h2 = node("h2", "GEOL 452 Structural Geology", 2, 90)
    nodes2 = (f2, h2) if header_only else (f2, d2, h2)
    edges2 = (
        (edge("f2", "h2", m.EdgeKind.NEXT_IN_READING_ORDER),)
        if header_only
        else (
            edge("f2", "d2", m.EdgeKind.NEXT_IN_READING_ORDER),
            edge("d2", "h2", m.EdgeKind.NEXT_IN_READING_ORDER),
        )
    )
    if ambiguous:
        nodes2 += (node("other", "another independent text stream.", 2, 48),)
    pages = tuple(
        m.PageGraph(
            physical_page=p,
            width=600 * scale,
            height=800 * scale,
            nodes=nodes,
            edges=edges,
            status=m.ResolutionStatus.CONFIRMED,
            contributing_engines=("SYNTHETIC",),
        )
        for p, nodes, edges in (
            (
                1,
                (h1, d1),
                (
                    edge("h1", "d1", m.EdgeKind.NEXT_IN_READING_ORDER),
                    edge("h1", "d1", m.EdgeKind.SAME_COLUMN),
                ),
            ),
            (2, nodes2, edges2),
        )
    )
    graph = m.DocumentGraph(
        identity=m.SourceDocumentIdentity(
            source_document_id="continuation-fixture",
            source_pdf_checksum="8" * 64,
            source_pdf_path="/synthetic.pdf",
            total_physical_pages=2,
        ),
        pages=pages,
    )
    candidates = []
    for cid, n, code, title in (
        ("c1", h1, "GEOL 451", "Mineral Deposits"),
        ("c2", h2, "GEOL 452", "Structural Geology"),
    ):
        candidates.append(
            m.HeaderCandidate(
                candidate_id=cid,
                physical_page=n.evidence[0].physical_page,
                node_ids=(n.node_id,),
                status=m.HeaderStatus.SUPPORTED,
                identifier_kind=m.HeaderIdentifierKind.MODERN_SUBJECT_CODE,
                arrangement=m.HeaderArrangement.INLINE,
                code_raw=code,
                title_raw=title,
                code_node_ids=(n.node_id,),
                title_node_ids=(n.node_id,),
                code_slice=m.NodeTextSlice(
                    node_id=n.node_id,
                    text_span=m.TextSpan(start=0, end=len(code)),
                    observed_text=code,
                ),
                title_slices=(
                    m.NodeTextSlice(
                        node_id=n.node_id,
                        text_span=m.TextSpan(start=len(code) + 1, end=len(n.raw_text)),
                        observed_text=title,
                    ),
                ),
                evidence=n.evidence,
            )
        )
    decisions = tuple(
        m.EntryDecision(
            candidate_id=c.candidate_id,
            status=m.EntryStatus.INDEPENDENT_COURSE,
            context_status=m.ResolutionStatus.CONFIRMED,
            reason_codes=("FIXTURE",),
            supporting_node_ids=c.node_ids,
            evidence=c.evidence,
        )
        for c in candidates
    )
    segments = tuple(
        m.BlockSegment(
            node_id=n.node_id,
            physical_page=1,
            role=n.role,
            raw_text=n.raw_text,
            evidence=n.evidence,
        )
        for n in (h1, d1)
    )
    raw = "\n".join(n.raw_text for n in (h1, d1))
    block = m.CourseBlock(
        block_id="block1",
        candidate_id="c1",
        physical_pages=(1,),
        segments=segments,
        node_ids=("h1", "d1"),
        start_node_id="h1",
        end_node_id="d1",
        raw_text=raw,
        normalized_text=" ".join(raw.split()),
        boundary_status=m.BoundaryStatus.UNRESOLVED,
        boundary_kind=m.BoundaryKind.PAGE_END_UNRESOLVED,
        boundary_reason_codes=("FIXTURE",),
        continuation_status=m.ContinuationStatus.UNRESOLVED,
        evidence=h1.evidence + d1.evidence,
        unresolved_evidence=d1.evidence,
    )
    profile = importlib.import_module(
        "catalog_mining.course_extraction_v3.document_profile"
    ).DocumentStyleProfile(
        source_document_id=graph.identity.source_document_id,
        status=m.ResolutionStatus.CONFIRMED,
    )
    styles = {n.node_id: LineStyle(size=12 * scale) for p in pages for n in p.nodes}
    return graph, profile, tuple(candidates), decisions, (block,), styles


# Run the synthetic continuation case through course detection and source ownership.
def run(reference, values, *, experimental=False, contextual=False):
    graph, profile, candidates, decisions, blocks, styles = values
    resolver = (
        ContextualContinuationResolver(reference.models, styles=styles)
        if contextual
        else AssessedContinuationResolver(
            reference.models, styles, allow_relative_attachment=experimental
        )
    )
    result = resolver.resolve(graph, profile, candidates, decisions, blocks)
    return result[0], resolver


# Check that verified reference continuation preserves exact source and excludes next header.
def test_verified_reference_continuation_preserves_exact_source_and_excludes_next_header(reference):
    result, resolver = run(reference, scenario(reference, source_y=730))
    assert result.node_ids == ("h1", "d1", "d2")
    assert result.physical_pages == (1, 2)
    assert result.boundary_node_ids == ("h2",)
    assert "GEOL 452" not in result.raw_text
    assert resolver.reports[0]["method"] == "EXISTING_STRUCTURAL_PROOF"
    assert resolver.reports[0]["decision"] == "CONTINUE"


# Check that relative margin candidate stays review only by default.
def test_relative_margin_candidate_stays_review_only_by_default(reference):
    values = scenario(reference)
    result, resolver = run(reference, values)
    assert result == values[4][0]
    report = resolver.reports[0]
    assert report["decision"] == "UNRESOLVED"
    assert report["proposed_decision"] == "CONTINUE"
    assert report["proposed_owned_node_ids"] == ["h1", "d1", "d2"]
    assert report["review_status"] == "PENDING_SOURCE_REVIEW"


# Check that experimental relative proof is scale invariant.
@pytest.mark.parametrize("scale", [0.5, 1, 2.5])
def test_experimental_relative_proof_is_scale_invariant(reference, scale):
    result, resolver = run(reference, scenario(reference, scale=scale), experimental=True)
    assert result.node_ids == ("h1", "d1", "d2")
    assert result.boundary_status == reference.models.BoundaryStatus.CONFIRMED
    assert resolver.reports[0]["method"] == "RELATIVE_SOURCE_STREAM_PROPOSAL"


# Check that multiple streams and missing page do not become continuation.
def test_multiple_streams_and_missing_page_do_not_become_continuation(reference):
    result, resolver = run(reference, scenario(reference, ambiguous=True), experimental=True)
    assert result.node_ids == ("h1", "d1")
    assert "UNIQUE_FIRST_SOURCE_STREAM_REQUIRED" in resolver.reports[0]["reasons"]
    values = list(scenario(reference))
    values[0] = values[0].model_copy(update={"pages": values[0].pages[:1]})
    values[2], values[3] = values[2][:1], values[3][:1]
    result, resolver = run(reference, values, experimental=True)
    assert result.node_ids == ("h1", "d1")
    assert resolver.reports[0]["reasons"] == ["ADJACENT_SOURCE_PAGE_UNAVAILABLE"]


# Check that verified header stops finished course unresolved header does not.
def test_verified_header_stops_finished_course_unresolved_header_does_not(reference):
    m = reference.models
    values = list(scenario(reference, header_only=True))
    block = values[4][0]
    segment = block.segments[-1]
    text = segment.raw_text + "."
    evidence = tuple(a.model_copy(update={"observed_text": text}) for a in segment.evidence)
    segment = segment.model_copy(update={"raw_text": text, "evidence": evidence})
    page = values[0].pages[0]
    n = page.nodes[-1].model_copy(update={"raw_text": text, "evidence": evidence})
    values[0] = values[0].model_copy(
        update={
            "pages": (
                page.model_copy(update={"nodes": (page.nodes[0], n)}),
                values[0].pages[1],
            )
        }
    )
    values[4] = (
        block.model_copy(
            update={
                "segments": (block.segments[0], segment),
                "raw_text": block.raw_text + ".",
                "normalized_text": block.normalized_text + ".",
                "evidence": block.segments[0].evidence + evidence,
                "unresolved_evidence": evidence,
            }
        ),
    )
    result, resolver = run(reference, values, contextual=True)
    assert result.node_ids == ("h1", "d1")
    assert result.boundary_status == m.BoundaryStatus.CONFIRMED
    assert resolver.continuation_proposals[0]["decision"] == "STOP"
    values[3] = (
        values[3][0],
        m.EntryDecision(
            candidate_id="c2",
            status=m.EntryStatus.UNRESOLVED,
            context_status=m.ResolutionStatus.UNRESOLVED,
            reason_codes=("FIXTURE",),
            contradicting_node_ids=("h2",),
        ),
    )
    result, resolver = run(reference, values, contextual=True)
    assert result.boundary_status == m.BoundaryStatus.UNRESOLVED
    assert resolver.continuation_proposals[0]["decision"] == "UNRESOLVED"
    assert (
        "TARGET_HEADER_OWNERSHIP_MUST_BE_RESOLVED_BEFORE_ATTACHMENT"
        in (resolver.continuation_proposals[0]["reasons"])
    )


# Check that fallback requires style order and downstream proof.
@pytest.mark.parametrize("failure", ["style", "font_missing", "order", "downstream"])
def test_fallback_requires_style_order_and_downstream_proof(reference, failure):
    m = reference.models
    values = list(scenario(reference))
    if failure == "style":
        values[5]["d2"] = LineStyle(size=24, bold=True)
    elif failure == "font_missing":
        values[5].pop("d2")
    elif failure == "order":
        values[0] = values[0].model_copy(
            update={
                "pages": (
                    values[0].pages[0],
                    values[0]
                    .pages[1]
                    .model_copy(
                        update={
                            "status": m.ResolutionStatus.UNRESOLVED,
                        }
                    ),
                )
            }
        )
    else:
        values[3] = (
            values[3][0],
            values[3][1].model_copy(
                update={
                    "status": m.EntryStatus.UNRESOLVED,
                }
            ),
        )
    result, resolver = run(reference, values, experimental=True)
    assert result.node_ids == ("h1", "d1")
    assert resolver.reports[0]["decision"] == "UNRESOLVED"


# Check that crop coverage does not merge text or assign ownership.
def test_crop_coverage_does_not_merge_text_or_assign_ownership(tmp_path):
    from test_crop_extractions import crop

    from course_parser.crop_extractions import render_crop
    from course_parser.io import sha256
    from course_parser.layout_models import Box

    _, frame, source = crop(tmp_path)
    right = render_crop(
        source, sha256(source), 1, Box(x0=20, y0=60, x1=185, y1=250), tmp_path / "right", dpi=144
    )
    report = assess_ocr_windows(frame, right)
    assert report["source_overlap_area_points2"] > 0
    assert report["decision"] == "UNRESOLVED"
    assert not report["text_concatenated"] and not report["course_ownership_assigned"]
    assert "OVERLAPPING_SOURCE_COVERAGE_MUST_NOT_DUPLICATE_TEXT" in report["reasons"]


# Check that cross column continuation is separate and preserves verified order.
def test_cross_column_continuation_is_separate_and_preserves_verified_order(reference):
    m = reference.models
    values = list(scenario(reference))
    graph = values[0]
    converted = []
    for node in graph.pages[1].nodes[1:]:
        anchor = node.evidence[0]
        geometry = anchor.geometry.model_copy(
            update={
                "x0": 320,
                "x1": 550,
            }
        )
        converted.append(
            node.model_copy(
                update={
                    "evidence": (
                        anchor.model_copy(update={"physical_page": 1, "geometry": geometry}),
                    )
                }
            )
        )
    d2, h2 = converted

    # Build a synthetic reading-order link between the requested original text pieces.
    def edge(a, b, kind):
        return m.GraphEdge(
            source_node_id=a,
            target_node_id=b,
            kind=kind,
            status=m.ResolutionStatus.CONFIRMED,
            reason="verified column transition",
        )

    page = graph.pages[0].model_copy(
        update={
            "nodes": (*graph.pages[0].nodes, d2, h2),
            "edges": (
                *graph.pages[0].edges,
                edge("d1", "d2", m.EdgeKind.NEXT_IN_READING_ORDER),
                edge("d2", "h2", m.EdgeKind.NEXT_IN_READING_ORDER),
                edge("d2", "h2", m.EdgeKind.SAME_COLUMN),
            ),
        }
    )
    values[0] = graph.model_copy(update={"pages": (page,)})
    values[2] = (
        values[2][0],
        values[2][1].model_copy(
            update={
                "physical_page": 1,
                "evidence": h2.evidence,
            }
        ),
    )
    block = values[4][0]
    values[4] = (
        block.model_copy(
            update={
                "boundary_kind": m.BoundaryKind.COLUMN_TRANSITION_UNRESOLVED,
                "boundary_node_ids": ("d2",),
                "boundary_evidence": d2.evidence,
                "excluded_neighbor_evidence": d2.evidence,
            }
        ),
    )
    result, resolver = run(reference, values, contextual=True)
    assert result.node_ids == ("h1", "d1", "d2")
    assert result.physical_pages == (1,)
    assert result.continuation_status == m.ContinuationStatus.CONFIRMED_CROSS_COLUMN
    assert resolver.continuation_proposals[0]["decision"] == "CONTINUE"
    assert resolver.continuation_proposals[0]["transitions"][0]["kind"] == "CROSS_COLUMN"


# Check that same page disjoint crop coverage is not order proof.
def test_same_page_disjoint_crop_coverage_is_not_order_proof(tmp_path):
    from test_crop_extractions import crop

    from course_parser.crop_extractions import render_crop
    from course_parser.io import sha256
    from course_parser.layout_models import Box

    _, frame, source = crop(tmp_path)
    right = render_crop(
        source, sha256(source), 1, Box(x0=20, y0=210, x1=185, y1=290), tmp_path / "right", dpi=144
    )
    report = assess_ocr_windows(frame, right)
    assert report["source_overlap_area_points2"] == 0
    assert report["decision"] == "UNRESOLVED"
    assert "DISJOINT_CROP_COVERAGE_IS_NOT_READING_ORDER_PROOF" in report["reasons"]
