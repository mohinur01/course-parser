# Tests for keeping every extracted character visible, including whitespace and unresolved
# text.
"""Loss, duplication and false ownership must fail whole-page retention."""

import copy
import importlib

import pytest
from test_contextual import extract
from test_continuation import run, scenario
from test_ownership_links import linked_fixture
from test_source_learning import _pilot_fixture
from test_table_structure import classify, table_layout

from course_parser.contextual import ContextualCourseFieldParser
from course_parser.io import read_jsonl
from course_parser.learning import run_learning_baseline
from course_parser.ownership import course_field_links
from course_parser.page_accounting import audit_page_accounting, build_page_accounting
from course_parser.table_structure import table_contexts


# Build a small test input with the source identity and values required by this test group.
def fixture(reference):
    courses, graph, candidates, decisions, blocks, _, _ = linked_fixture(reference)
    ledger = build_page_accounting(
        graph, blocks, courses, candidates=candidates, decisions=decisions
    )
    return graph, blocks, courses, ledger


# Check that every cell has lossless rows fields and unowned dispositions.
def test_every_cell_has_lossless_rows_fields_and_unowned_dispositions(reference):
    graph, _, courses, ledger = fixture(reference)
    audit = ledger["audit"]
    assert audit["status"] == "PASS"
    assert audit["source_cells"] == audit["source_rows"] == 6
    assert audit["accounted_characters"] == audit["source_characters"]
    assert {s["role"] for r in ledger["rows"] for s in r["spans"] if s["role"]} >= {
        "CODE",
        "TITLE",
        "DESCRIPTION",
        "CREDITS",
        "PREREQUISITES",
        "SEPARATOR",
    }
    for p in graph.pages:
        for n in p.nodes:
            rows = [r for r in ledger["rows"] if r["node_id"] == n.node_id]
            assert "".join(s["raw_text"] for r in rows for s in r["spans"]) == n.raw_text
    assert ledger["rows"][0]["spans"][0]["disposition"] == "SECTION_HEADER"
    assert len(courses) == 2
    assert not ledger["semantic_truth_assigned"]
    assert not ledger["source_extraction_completeness_verified"]


# Preserve newlines, whitespace, Unicode and empty text cells even when no course is
# extracted.
def test_newline_whitespace_unicode_and_empty_cells_survive_without_courses(reference):
    _, graph, _, _, _, _, _ = extract(
        reference, ["alpha\r\n\nβeta\n", "", "  "], return_stages=True
    )
    ledger = build_page_accounting(graph, (), ())
    assert ledger["audit"]["source_rows"] == 7
    assert ledger["audit"]["unresolved_rows"] == 5
    assert [r["raw_text"] for r in ledger["rows"] if r["node_id"] == "n1"] == [
        "alpha\r\n",
        "\n",
        "βeta\n",
    ]
    assert ledger["audit"]["source_characters"] == ledger["audit"]["accounted_characters"]


# Check that corrupt accounting cannot pass.
@pytest.mark.parametrize(
    "mutation",
    [
        "delete",
        "duplicate",
        "text",
        "gap",
        "overlap",
        "evidence",
        "owner",
        "role",
        "relabel",
        "scope",
    ],
)
def test_corrupt_accounting_cannot_pass(reference, mutation):
    graph, blocks, courses, ledger = fixture(reference)
    ledger = copy.deepcopy(ledger)
    row = next(
        r for r in ledger["rows"] if any(s["disposition"] == "COURSE_FIELD" for s in r["spans"])
    )
    span = row["spans"][0]
    if mutation == "delete":
        ledger["rows"].remove(row)
    elif mutation == "duplicate":
        ledger["rows"][-1] = row
    elif mutation == "text":
        row["raw_text"] += "invented"
    elif mutation == "gap":
        span["text_span"]["start"] += 1
    elif mutation == "overlap":
        row["spans"].insert(0, copy.deepcopy(span))
    elif mutation == "evidence":
        span["evidence"] = []
    elif mutation == "owner":
        span["course_occurrence_id"] = "another-course"
    elif mutation == "role":
        row["interpreted_node_role"] = "PAGE_FURNITURE"
    elif mutation == "scope":
        ledger["scope"] = "OWNED_COURSES_ONLY"
    else:
        span.update(
            disposition="UNRESOLVED",
            course_occurrence_id=None,
            block_id=None,
            allocation_id=None,
            role=None,
            record_fields=[],
            record_field_statuses={},
        )
    with pytest.raises(ValueError):
        audit_page_accounting(graph, ledger, field_links=course_field_links(graph, blocks, courses))


# Check that native inventory loss or mutation cannot be hidden by adopted graph.
def test_native_inventory_loss_or_mutation_cannot_be_hidden_by_adopted_graph(reference):
    graph, _, _, _ = fixture(reference)
    page = graph.pages[0]
    for nodes in (
        page.nodes[1:],
        (page.nodes[0].model_copy(update={"raw_text": "changed"}), *page.nodes[1:]),
    ):
        changed = graph.model_copy(update={"pages": (page.model_copy(update={"nodes": nodes}),)})
        with pytest.raises(ValueError, match="accounting graph"):
            build_page_accounting(changed, (), (), source_graph=graph)


# Check that withheld block and duplicate course owner.
def test_withheld_block_and_duplicate_course_owner(reference):
    graph, blocks, courses, _ = fixture(reference)
    ledger = build_page_accounting(graph, blocks, ())
    owned_ids = {nid for b in blocks for nid in b.node_ids}
    assert all(
        s["disposition"] == "UNRESOLVED"
        for r in ledger["rows"]
        if r["node_id"] in owned_ids
        for s in r["spans"]
    )
    second = courses[0].model_copy(update={"course_occurrence_id": "shared-without-contract"})
    with pytest.raises(ValueError, match="shared contract"):
        build_page_accounting(graph, blocks, (*courses, second))
    with pytest.raises(ValueError, match="duplicate document node"):
        build_page_accounting(
            graph.model_copy(
                update={
                    "pages": (
                        graph.pages[0],
                        graph.pages[0].model_copy(update={"physical_page": 2}),
                    )
                }
            ),
            (),
            (),
        )


# Check that tables and requirements remain visible without course ownership.
@pytest.mark.parametrize("requirements", [False, True])
def test_tables_and_requirements_remain_visible_without_course_ownership(reference, requirements):
    layout = table_layout(
        reference, heading="Degree Requirements" if requirements else "Course Descriptions"
    )
    graph, _, candidates, decisions = classify(reference, layout)
    ledger = build_page_accounting(
        graph,
        (),
        (),
        candidates=candidates,
        decisions=decisions,
        table_contexts=table_contexts((layout,)),
    )
    cells = [
        s for r in ledger["rows"] if r["interpreted_node_role"] == "TABLE_CELL" for s in r["spans"]
    ]
    assert cells
    assert all(s["disposition"] == ("REQUIREMENT_TEXT" if requirements else "TABLE") for s in cells)
    assert ledger["audit"]["accounted_characters"] == sum(
        len(n.raw_text) for p in graph.pages for n in p.nodes
    )


# Check that cross page continuation has one owner and retains furniture and next header.
def test_cross_page_continuation_has_one_owner_and_retains_furniture_and_next_header(reference):
    values = scenario(reference, source_y=730)
    graph, profile, candidates, _, _, styles = values
    block, _ = run(reference, values)
    courses = ContextualCourseFieldParser(reference.models, styles).parse(
        graph, profile, candidates, (block,)
    )
    assert courses
    ledger = build_page_accounting(graph, (block,), courses)
    rows = {r["node_id"]: r for r in ledger["rows"]}
    assert (
        rows["d1"]["spans"][0]["course_occurrence_id"]
        == rows["d2"]["spans"][0]["course_occurrence_id"]
    )
    assert rows["f2"]["spans"][0]["disposition"] == "PAGE_FURNITURE"
    assert rows["h2"]["spans"][0]["disposition"] == "UNRESOLVED"
    assert len(ledger["audit"]["pages"]) == 2


# Check that learning requires accounting and preserves native rows on field failure.
@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_learning_requires_accounting_and_preserves_native_rows_on_field_failure(
    tmp_path, reference, monkeypatch, contextual, failure
):
    pilot, _, _, _ = _pilot_fixture(tmp_path, reference)
    output = tmp_path / "predictions"
    if failure:
        cls = (
            ContextualCourseFieldParser
            if contextual
            else importlib.import_module(
                "catalog_mining.course_extraction_v3.field_parser"
            ).StructuralCourseFieldParser
        )

        # Raise a deliberate error to test how the caller retains evidence after a failure.
        def fail(*args, **kwargs):
            raise ValueError("deliberate downstream failure")

        monkeypatch.setattr(cls, "parse", fail)
    result = run_learning_baseline(reference, pilot, output, contextual_course_assembly=contextual)
    assert result["whole_page_accounting_required"] is True
    proposal = read_jsonl(output / "page_proposals.jsonl")[0]
    ledger = proposal["whole_page_accounting"]
    assert ledger["audit"]["status"] == "PASS"
    assert ledger["audit"]["source_cells"] == sum(
        len(p["regions"]) for p in proposal["source_layouts"]
    )
    assert ledger["audit"]["source_characters"] == ledger["audit"]["accounted_characters"]
    assert read_jsonl(output / "page_accounting.jsonl")[0]["whole_page_accounting"] == ledger
    if failure:
        assert proposal["status"] == "PARSER_STAGE_FAILED"
        assert ledger["failure_fallback_source_only"] is True
        assert not read_jsonl(output / "course_proposals.jsonl")
    else:
        assert proposal["status"] == "PROPOSED"


# Check that native extraction failure preserves prior pages and reports missing context.
def test_native_extraction_failure_preserves_prior_pages_and_reports_missing_context(
    tmp_path, reference, monkeypatch
):
    pilot, _, _, _ = _pilot_fixture(tmp_path, reference)
    cls = importlib.import_module(
        "catalog_mining.course_extraction_v3.layout_baseline"
    ).PyMuPDFGeometryLayoutEngine
    original = cls.analyze_packet

    # Raise a deliberate extraction error on the chosen page and delegate other pages
    # normally.
    def fail_page(self, packet, **kwargs):
        # Simulate a native-extraction failure to exercise the error-handling path.
        if kwargs["physical_page"] == 2:
            raise ValueError("deliberate native extraction failure")
        return original(self, packet, **kwargs)

    monkeypatch.setattr(cls, "analyze_packet", fail_page)
    output = tmp_path / "predictions"
    run_learning_baseline(reference, pilot, output)
    proposal = read_jsonl(output / "page_proposals.jsonl")[0]
    ledger = proposal["whole_page_accounting"]
    assert proposal["status"] == "PARSER_STAGE_FAILED"
    assert ledger["context_inventory_status"] == "PARTIAL_OR_UNAVAILABLE"
    assert ledger["unavailable_context_pages"] == [2, 3]
    assert {r["physical_page"] for r in ledger["rows"]} == {1}
    assert not read_jsonl(output / "course_proposals.jsonl")


# Check that empty page inventory is retained and not misreported as text recovery.
def test_empty_page_inventory_is_retained_and_not_misreported_as_text_recovery(reference):
    graph, _, _, _ = fixture(reference)
    graph = graph.model_copy(
        update={"pages": (graph.pages[0].model_copy(update={"nodes": (), "edges": ()}),)}
    )
    ledger = build_page_accounting(graph, (), ())
    assert ledger["rows"] == []
    assert ledger["audit"]["pages"][0]["source_inventory_status"] == "EMPTY_SOURCE_INVENTORY"
    assert ledger["source_extraction_completeness_verified"] is False


# Check that cached accounting rejects reserved paths before read.
@pytest.mark.parametrize("reserved_arg", ["pilot", "evaluation", "output"])
def test_cached_accounting_rejects_reserved_paths_before_read(tmp_path, monkeypatch, reserved_arg):
    from pathlib import Path

    from course_parser import page_accounting_audit as module

    # Fail the test if a guarded operation attempts to read a prohibited payload.
    def forbidden(*args, **kwargs):
        pytest.fail("attempted payload read before rejecting reserved path")

    monkeypatch.setattr(module, "read_json", forbidden)
    monkeypatch.setattr(module, "read_jsonl", forbidden)
    paths = {key: tmp_path / key for key in ("pilot", "evaluation", "output")}
    paths[reserved_arg] = Path("artifacts/validation-transfer-v1/heldout-review")
    with pytest.raises(ValueError, match="reserved"):
        module.audit_cached_pages(None, **paths)


# Check that cached accounting rejects unregistered evaluation before source read.
def test_cached_accounting_rejects_unregistered_evaluation_before_source_read(
    tmp_path, monkeypatch
):
    from course_parser import page_accounting_audit as module

    # Fail the test if a guarded operation attempts to read a prohibited payload.
    def forbidden(*args, **kwargs):
        pytest.fail("attempted cached source read before policy check")

    monkeypatch.setattr(module, "read_jsonl", forbidden)
    with pytest.raises(ValueError):
        module.audit_cached_pages(
            None, tmp_path / "pilot", tmp_path / "unregistered-evaluation", tmp_path / "out"
        )


# Check that cached window cannot lose native page or source identity.
def test_cached_window_cannot_lose_native_page_or_source_identity(reference):
    from course_parser.page_accounting_audit import account_cached_window

    layout = table_layout(reference).model_dump(mode="json")
    item = {
        "dataset_page_id": "test",
        "context_physical_pages": [1],
        "central_physical_page": 1,
        "source_pdf_checksum": "a" * 64,
    }
    proposal = {
        "dataset_page_id": "test",
        "source_truth_read": False,
        "status": "PROPOSED",
        "source_layouts": [layout],
        "layouts": [layout],
        "blocks": [],
        "candidates": [],
        "decisions": [],
    }
    assert account_cached_window(reference, item, proposal, [])["audit"]["source_cells"] == 9
    changed = copy.deepcopy(proposal)
    changed["layouts"][0]["regions"].pop()
    with pytest.raises(ValueError):
        account_cached_window(reference, item, changed, [])
    with pytest.raises(ValueError, match="bound context"):
        account_cached_window(reference, {**item, "source_pdf_checksum": "b" * 64}, proposal, [])
