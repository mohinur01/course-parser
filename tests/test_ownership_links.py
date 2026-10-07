# Tests for linking each field to its original text without conflicting ownership claims.
"""Competing title ownership, lossless links, and legitimate split headers."""

from types import SimpleNamespace

import pytest
from test_contextual import extract

from course_parser.ownership import course_field_links


# Check that body reference cannot borrow next course title and description.
def test_body_reference_cannot_borrow_next_course_title_and_description(reference):
    records, _, candidates, decisions, _, _, _ = extract(
        reference,
        [
            "GEOL 101 Mineral Systems",
            "Study of mineral systems and field measurements. Prerequisite:",
            "GEOL 100.",
            "GEOL 102 Resource Geology (3)",
            "Students interpret geological resources and practice exploration methods.",
        ],
        return_stages=True,
    )
    assert {r.code.raw for r in records} == {"GEOL 101", "GEOL 102"}
    conflict = next(c for c in candidates if c.code_raw == "GEOL 100.")
    assert str(conflict.status) == "UNRESOLVED"
    assert "TITLE_OVERLAPS_COMPETING_HEADER" in conflict.contradictions
    decision = next(d for d in decisions if d.candidate_id == conflict.candidate_id)
    assert str(decision.status) != "INDEPENDENT_COURSE"
    second = next(r for r in records if r.code.raw == "GEOL 102")
    assert second.title.raw.strip() == "Resource Geology"
    assert second.description.raw.startswith("Students interpret")


# Check that split title without a competing header remains supported.
def test_split_title_without_a_competing_header_remains_supported(reference):
    records, _, _ = extract(
        reference,
        [
            "GEOL 101",
            "Mineral Systems",
            "Students interpret geological resources and practice exploration methods.",
            "GEOL 102 Resource Geology",
            "Students examine resources and interpret their geological context.",
        ],
    )
    first = next(r for r in records if r.code.raw == "GEOL 101")
    assert first.title.raw == "Mineral Systems"


# Check that printed embedded code list is a reference not a new entry.
def test_printed_embedded_code_list_is_a_reference_not_a_new_entry(reference):
    records, candidates, decisions = extract(
        reference,
        [
            "GEOL 101 Exploration Techniques",
            "Students study techniques. Options: BIOL 100 Microscopy, BIOL 101 Imaging,",
            "BIOL 102 Mapping, BIOL 103 Sampling, BIOL 104 Surveying,",
            "BIOL 105 Analysis, BIOL 106 Interpretation. Other options may be added.",
            "GEOL 102 Resource Geology",
            "Study of geological resources and exploration methods.",
        ],
    )
    assert not any(c.code.raw.startswith("BIOL") for c in records)
    listed = next(c for c in candidates if c.code_raw == "BIOL 102")
    d = next(d for d in decisions if d.candidate_id == listed.candidate_id)
    assert str(d.status) == "COURSE_REFERENCE"
    assert "SOURCE_EMBEDDED_COMMA_SEPARATED_CODE_LIST" in d.reason_codes


# Check that unbolded courses without credits are not blocked by list check.
def test_unbolded_courses_without_credits_are_not_blocked_by_list_check(reference):
    records, _, decisions = extract(
        reference,
        [
            "BIOL 101 Mineral Systems",
            "Students study mineral systems and investigate their origin.",
            "BIOL 102 Resource Geology",
            "Study of geological resources and exploration methods.",
        ],
    )
    assert {c.code.raw for c in records} == {"BIOL 101", "BIOL 102"}
    assert not any(
        "SOURCE_EMBEDDED_COMMA_SEPARATED_CODE_LIST" in d.reason_codes for d in decisions
    )


# Build two synthetic courses with exact source links, including a course without printed
# credits.
def linked_fixture(reference):
    return extract(
        reference,
        [
            "GEOL 101 Mineral Systems (3)",
            "Study of mineral systems. Prerequisite: GEOL 100.",
            "GEOL 102 Resource Geology",
            "Study of geological resources and exploration methods.",
        ],
        return_stages=True,
    )


# Check that links reconstruct each native node and keep record statuses.
def test_links_reconstruct_each_native_node_and_keep_record_statuses(reference):
    records, graph, _, _, blocks, _, _ = linked_fixture(reference)
    links = course_field_links(graph, blocks, records)
    assert links == course_field_links(graph, blocks, records)
    assert {link["role"] for link in links} >= {"CODE", "TITLE", "DESCRIPTION", "CREDITS"}
    assert all(link["status"] == "MODEL_PROPOSAL" for link in links)
    nodes = {n.node_id: n for p in graph.pages for n in p.nodes}
    for node_id in {link["node_id"] for link in links}:
        owned = [link for link in links if link["node_id"] == node_id]
        assert len({link["course_occurrence_id"] for link in owned}) == 1
        assert "".join(link["raw_text"] for link in owned) == nodes[node_id].raw_text
    for link in links:
        assert link["source_pdf_checksum"] == graph.identity.source_pdf_checksum
        assert set(link["record_field_statuses"]) == set(link["record_fields"])


# Check that links reject corrupted or unowned source.
@pytest.mark.parametrize("mutation", ["source_text", "geometry", "outside_block", "gap"])
def test_links_reject_corrupted_or_unowned_source(reference, mutation):
    records, graph, _, _, blocks, _, _ = linked_fixture(reference)
    course = records[0]
    allocation = course.field_allocations[0]
    if mutation == "source_text":
        allocation = allocation.model_copy(update={"segment_raw_text": "invented source"})
    elif mutation == "geometry":
        anchor = allocation.evidence[0].model_copy(update={"geometry": None})
        allocation = allocation.model_copy(update={"evidence": (anchor,)})
    elif mutation == "outside_block":
        allocation = allocation.model_copy(update={"node_id": "n0"})
    if mutation == "gap":
        values = course.field_allocations[1:]
    else:
        values = (allocation, *course.field_allocations[1:])
    # Deliberately bypass the record model here: the export must independently verify
    # relationships against the real graph, even for malformed input.
    bad = SimpleNamespace(
        **{**course.__dict__, "field_allocations": values},
    )
    with pytest.raises(ValueError):
        course_field_links(graph, blocks, [bad, *records[1:]])
