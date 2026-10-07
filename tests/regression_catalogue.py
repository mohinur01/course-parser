# Verify the pinned regression fixtures, required test coverage and known recovery and
# ambiguity contracts.
"""Test-only catalogue: pinned permitted fixtures and explicit behavioral obligations."""

from pathlib import Path

from course_parser.io import read_json, sha256, verify_files

ROOT = Path(__file__).resolve().parents[1]


# Read the permanent regression catalogue and reject empty coverage or an invalid scope.
def catalogue(root=ROOT):
    value = read_json(root / "config/regression_suite_v1.json")
    # Reject invalid regression scope or empty coverage.
    if (
        value["scope"] != "PERMITTED_FROZEN_REGRESSIONS_ONLY"
        or value["protected_evaluation_opened"] is not False
        or not value["coverage"]
        or not value["fixture_inventories"]
    ):
        raise ValueError("invalid regression scope or empty coverage")
    return value


# Verify the pinned regression directory, manifest and source files before returning the
# approved cases.
def fixture_cases(folder, *, root=ROOT, registry=None):
    registry = registry or catalogue(root)
    inventory = registry["fixture_inventories"][folder]
    data = root.resolve() / "tests/data"
    directory = data / folder
    # Reject escapes before opening any manifest or source payload.
    # Reject a path or coordinate that falls outside its allowed source boundary.
    if (
        Path(folder).name != folder
        or data.resolve() != data
        or directory.resolve().parent != data
        or directory.is_symlink()
    ):
        raise ValueError("regression fixture escapes permitted test data")
    manifest = directory / "manifest.json"
    # Stop if the frozen regression manifest changed.
    if manifest.is_symlink() or sha256(manifest) != inventory["manifest_sha256"]:
        raise ValueError("frozen regression manifest changed")
    hashes = read_json(manifest)["file_sha256"]
    names = inventory["cases"]
    # The saved fixture list must still match and contain at least one case.
    if not names or len(set(names)) != len(names) or set(hashes) != set(names):
        raise ValueError("regression fixture inventory changed or empty")
    # Use only registered regression fixtures, and require each one to be present.
    if {p.name for p in directory.glob("*.json")} != {"manifest.json", *names}:
        raise ValueError("unregistered or missing regression fixture")
    for name in names:
        # Reject a path or coordinate that falls outside its allowed source boundary.
        if Path(name).name != name or (directory / name).is_symlink():
            raise ValueError("regression fixture file escapes pinned inventory")
    verify_files(directory, hashes)
    return [directory / name for name in names]


# Check every registered regression fixture and return only the cases whose saved fingerprints
# still match.
def verify_catalogue(root=ROOT):
    registry = catalogue(root)
    return [
        case
        for folder in registry["fixture_inventories"]
        for case in fixture_cases(folder, root=root, registry=registry)
    ]


# Match each required regression obligation to collected test identifiers and reject missing
# coverage.
def coverage_matches(nodeids, registry):
    matches = {}
    for group, selectors in registry["coverage"].items():
        # Each required regression group needs a nonempty, unique set of test selectors.
        if not selectors or len(set(selectors)) != len(selectors):
            raise ValueError(f"empty or duplicate regression obligation: {group}")
        matches[group] = []
        for selector in selectors:
            expected = "tests/" + selector
            found = [
                n for n in nodeids if n.split("[")[0] == expected or n.startswith(expected + "::")
            ]
            # Reject missing regression test: group: selector.
            if not found:
                raise ValueError(f"missing regression test: {group}: {selector}")
            matches[group].extend(found)
    return matches


# A same-count replacement cannot conceal loss of an existing recovered course.
def check_replay_contract(contract, recovered, unaligned, ambiguous):
    """A same-count replacement cannot conceal loss of an existing recovered course."""
    missing = set(contract["required_existing_gold_ids"]) - set(recovered)
    # Previously recovered reviewed courses must still be present; an equal total count cannot
    # hide a replacement.
    if missing:
        raise ValueError(f"lost source-reviewed course regressions: {sorted(missing)}")
    # Reject a new emitted course that matches no reviewed entry and is not an acknowledged
    # ambiguity.
    if set(unaligned) - set(ambiguous):
        raise ValueError("new unaligned nonambiguous emitted course")
    # Keep ambiguous emitted headings within the recorded regression limit.
    if len(unaligned) > contract["maximum_ambiguous_emitted_headers"]:
        raise ValueError("source regression ambiguity ceiling exceeded")
