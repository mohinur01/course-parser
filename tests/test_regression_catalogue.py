# Tests for the permanent regression inventory, required coverage and known recovery
# guarantees.
"""Regression gates cannot pass after fixture loss, answer drift or same-count swaps."""

import json

import pytest
from regression_catalogue import (
    catalogue,
    check_replay_contract,
    coverage_matches,
    fixture_cases,
    verify_catalogue,
)

from course_parser.io import sha256


# Create a small pinned fixture inventory for testing checksum and catalogue safeguards.
def frozen_fixture(tmp_path):
    folder = tmp_path / "tests/data/example"
    folder.mkdir(parents=True)
    (folder / "case.json").write_text('{"truth_basis":"fixture"}')
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps({"file_sha256": {"case.json": sha256(folder / "case.json")}}))
    registry = {
        "fixture_inventories": {
            "example": {"manifest_sha256": sha256(manifest), "cases": ["case.json"]}
        }
    }
    return folder, registry


# Check that permitted source inventory is complete and pinned.
def test_permitted_source_inventory_is_complete_and_pinned():
    registry = catalogue()
    assert len(verify_catalogue()) == 17
    assert len(registry["source_case_contracts"]) == 12
    contracts = registry["source_case_contracts"].values()
    assert sum(len(x["required_existing_gold_ids"]) for x in contracts) == 76
    assert registry["protected_evaluation_opened"] is False


# Check that frozen source cases cannot disappear or be rewritten.
@pytest.mark.parametrize("mutation", ["deleted", "changed", "extra", "rehash"])
def test_frozen_source_cases_cannot_disappear_or_be_rewritten(tmp_path, mutation):
    folder, registry = frozen_fixture(tmp_path)
    path = folder / "case.json"
    if mutation == "deleted":
        path.unlink()
    elif mutation == "extra":
        (folder / "new.json").write_text("{}")
    else:
        path.write_text("new answer")
        if mutation == "rehash":
            (folder / "manifest.json").write_text(
                json.dumps({"file_sha256": {"case.json": sha256(path)}})
            )
    with pytest.raises(ValueError):
        fixture_cases("example", root=tmp_path, registry=registry)


# Check that symlink cannot redirect source case outside permitted data.
def test_symlink_cannot_redirect_source_case_outside_permitted_data(tmp_path):
    folder, registry = frozen_fixture(tmp_path)
    outside = tmp_path / "unopened.json"
    outside.write_text("unopened invalid payload")
    (folder / "case.json").unlink()
    (folder / "case.json").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes pinned inventory"):
        fixture_cases("example", root=tmp_path, registry=registry)


# Check that missing test obligation is not silent deselection.
def test_missing_test_obligation_is_not_silent_deselection():
    registry = {"coverage": {"required": ["test_example.py::test_fixed_bug"]}}
    with pytest.raises(ValueError, match="missing regression test"):
        coverage_matches(["tests/test_example.py::test_unrelated"], registry)
    assert coverage_matches(["tests/test_example.py::test_fixed_bug[rotation-90]"], registry)


# Check that equal completion count cannot replace a known recovered course.
def test_equal_completion_count_cannot_replace_a_known_recovered_course():
    contract = {
        "required_existing_gold_ids": ["source-course-a", "source-course-b"],
        "maximum_ambiguous_emitted_headers": 0,
    }
    with pytest.raises(ValueError, match="lost source-reviewed course"):
        check_replay_contract(contract, {"source-course-a", "source-course-c"}, set(), set())
    check_replay_contract(
        contract, {"source-course-a", "source-course-b", "source-course-c"}, set(), set()
    )


# Check new false-course output and increased ambiguity as separate regression failures.
def test_false_emission_and_growth_in_known_ambiguity_fail_separately():
    contract = {"required_existing_gold_ids": [], "maximum_ambiguous_emitted_headers": 1}
    with pytest.raises(ValueError, match="nonambiguous"):
        check_replay_contract(contract, set(), {"reference"}, set())
    with pytest.raises(ValueError, match="ambiguity ceiling"):
        check_replay_contract(contract, set(), {"a", "b"}, {"a", "b"})
    check_replay_contract(contract, set(), {"known-ambiguity"}, {"known-ambiguity"})
