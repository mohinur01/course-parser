# Check how evaluation pages are grouped by institution and catalog family. Keep related
# sources together and prevent development commands from reading reserved evaluation data.
"""Metadata-only checks for exposed development splits; never open reserved cohorts."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from .io import artifact_hashes, contained_file, read_json, read_jsonl, sha256, write_json

VERSION = "course-parser-evaluation-design-v1"


# Map legacy development partition names to their explicit tuning or exposed-diagnostic role.
def evaluation_role(partition: str) -> str:
    if partition == "TUNING":
        return "TUNING"
    if partition in {"VALIDATION", "DIAGNOSTIC", "EXPOSED_DIAGNOSTIC"}:
        return "EXPOSED_DIAGNOSTIC"
    raise ValueError("only TUNING and exposed diagnostic partitions are permitted during phases")


# Retain identities needed for leakage checks, excluding source text and answers.
def split_metadata(item: dict) -> dict:
    """Retain identities needed for leakage checks, excluding source text and answers."""
    role = evaluation_role(item["learning_partition"])
    # The evaluation role must agree with the sample's original partition.
    if item.get("evaluation_role", role) != role:
        raise ValueError("evaluation role contradicts legacy partition")
    alias_ids = item.get("institution_alias_ids", [])
    # Reject invalid stable institution aliases.
    if not isinstance(alias_ids, list) or any(not isinstance(x, str) or not x for x in alias_ids):
        raise ValueError("invalid stable institution aliases")
    aliases = set(alias_ids)
    for identity in (item, item.get("original_reported_identity", {})):
        for key in ("institution_id", "institution_family_id"):
            if identity.get(key):
                aliases.add(identity[key])
    return {
        **{
            key: item[key]
            for key in (
                "dataset_page_id",
                "split",
                "learning_group",
                "learning_partition",
                "source_pdf_checksum",
                "central_physical_page",
                "context_physical_pages",
            )
        },
        "institution_alias_ids": sorted(aliases),
        "catalog_family_id": item.get("catalog_family_id"),
        "selection_role": item.get("selection_role"),
        "pilot_role": item.get("pilot_role"),
    }


# Check transitive family isolation and count correlated windows, without labels.
def audit_split_metadata(items: list[dict]) -> dict:
    """Check transitive family isolation and count correlated windows, without labels."""
    # Require metadata before checking how sources are divided between evaluation roles.
    if not items:
        raise ValueError("empty split metadata")
    pages = [split_metadata(x) for x in items]
    # Reject duplicate metadata page identity.
    if len({x["dataset_page_id"] for x in pages}) != len(pages):
        raise ValueError("duplicate metadata page identity")
    parent = list(range(len(pages)))

    # Find the shared representative for linked aliases so the same institution family stays
    # together.
    def find(i):
        # Follow alias links to the shared family representative, shortening the chain
        # as we go.
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owners, contexts, central = {}, defaultdict(list), set()
    for i, page in enumerate(pages):
        # Protected page split cannot enter a development metadata audit.
        if page["split"] != "DEVELOPMENT":
            raise ValueError("protected page split cannot enter a development metadata audit")
        evaluation_role(page["learning_partition"])
        # Reject missing stable institution family.
        if not isinstance(page["learning_group"], str) or not page["learning_group"]:
            raise ValueError("missing stable institution family")
        # Reject missing stable institution identity or aliases.
        if not page["institution_alias_ids"] or any(
            not isinstance(x, str) or not x for x in page["institution_alias_ids"]
        ):
            raise ValueError("missing stable institution identity or aliases")
        checksum = page["source_pdf_checksum"]
        # Reject invalid source PDF identity.
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("invalid source PDF identity")
        context = page["context_physical_pages"]
        # Reject invalid physical context window.
        if (
            not isinstance(context, list)
            or not context
            or any(type(x) is not int or x < 1 for x in context)
            or len(context) != len(set(context))
            or type(page["central_physical_page"]) is not int
            or page["central_physical_page"] not in context
        ):
            raise ValueError("invalid physical context window")
        physical = (checksum, page["central_physical_page"])
        # Reject duplicate physical central page.
        if physical in central:
            raise ValueError("duplicate physical central page")
        central.add(physical)
        keys = [("group", page["learning_group"]), ("pdf", checksum)]
        keys.extend(("institution", alias) for alias in page["institution_alias_ids"])
        family = page["catalog_family_id"]
        if family is not None:
            # Reject invalid declared catalog family.
            if not isinstance(family, str) or not family:
                raise ValueError("invalid declared catalog family")
            keys.append(("catalog_family", family))
        for physical_page in context:
            keys.append(("context", checksum, physical_page))
            contexts[checksum, physical_page].append(i)
        for key in keys:
            if key in owners:
                parent[find(i)] = find(owners[key])
            else:
                owners[key] = i
    components = defaultdict(list)
    for i, page in enumerate(pages):
        components[find(i)].append(page)
    for component in components.values():
        # Keep related institutions, catalogs, PDFs and overlapping page windows in the same
        # evaluation role.
        if len({evaluation_role(x["learning_partition"]) for x in component}) != 1:
            raise ValueError("institution/catalog/PDF/context family crosses evaluation roles")
    membership = {
        page["dataset_page_id"]: min(x["learning_group"] for x in component)
        for component in components.values()
        for page in component
    }
    overlapping_pairs = {
        (min(a, b), max(a, b))
        for members in contexts.values()
        for a in members
        for b in members
        if a != b
    }
    rows = [
        {
            "dataset_page_id": x["dataset_page_id"],
            "legacy_partition": x["learning_partition"],
            "evaluation_role": evaluation_role(x["learning_partition"]),
            "generalization_claim_permitted": False,
        }
        for x in pages
    ]
    return {
        "schema_version": VERSION,
        "status": "EXPOSED_METADATA_SPLIT_CHECKS_PASS",
        "windows": len(pages),
        "role_counts": dict(Counter(x["evaluation_role"] for x in rows)),
        "connected_families": len(components),
        "family_membership": dict(sorted(membership.items())),
        "unique_physical_context_pages": len(contexts),
        "overlapping_context_window_pairs": len(overlapping_pairs),
        "declared_catalog_family_windows": sum(x["catalog_family_id"] is not None for x in pages),
        "selection_role_counts": dict(Counter(x["selection_role"] or "UNSPECIFIED" for x in pages)),
        "pilot_role_counts": dict(Counter(x["pilot_role"] or "UNSPECIFIED" for x in pages)),
        "unknown_catalog_template_similarity": True,
        "generalization_claim_permitted": False,
        "protected_cohort_isolation_verified": False,
        "protected_evaluation_opened": False,
        "pages": rows,
        "limits": [
            "Only supplied stable identities, aliases and catalog-family metadata are checked.",
            "No unseen template similarity or reserved-cohort isolation was inspected.",
            "Correlated context windows are not independent evaluation observations.",
            "Exposed institution separation cannot establish untouched generalization.",
        ],
    }


# Locate an approved metadata file without allowing a path to escape its artifact directory.
def _metadata_file(root: Path, name: str) -> Path:
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(root / name)
    return contained_file(root, name)


# Require a registered exposed evaluation before reading its pilot metadata.
def load_exposed_split_metadata(pilot: Path, evaluation: Path, output: Path) -> list[dict]:
    """Require a registered exposed evaluation before reading its pilot metadata."""
    from .field_scoring import enforce_policy
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(pilot, evaluation, output)
    enforce_policy(evaluation, output)
    evaluation_manifest = read_json(_metadata_file(evaluation, "evaluation_manifest.json"))
    manifest_path = _metadata_file(pilot, "pilot_manifest.json")
    # Stop if the metadata pilot differs from approved exposed evaluation.
    if sha256(manifest_path) != evaluation_manifest["pilot_manifest_sha256"]:
        raise ValueError("metadata pilot differs from approved exposed evaluation")
    manifest = read_json(manifest_path)
    # Stop if the metadata pilot is not exposed DEVELOPMENT.
    if manifest["split"] != "DEVELOPMENT" or manifest["protected_evaluation_opened"] is not False:
        raise ValueError("metadata pilot is not exposed DEVELOPMENT")
    selected = _metadata_file(pilot, "selected_pages.jsonl")
    # Reject this input if its file fingerprint no longer matches the recorded version.
    if sha256(selected) != manifest["file_sha256"].get("selected_pages.jsonl"):
        raise ValueError("selected source metadata checksum changed")
    return [split_metadata(x) for x in read_jsonl(selected)]


# CLI preflight: reject reserved paths and protected metadata before answer import.
def guard_development_scoring(pilot: Path, *paths: Path) -> dict:
    """CLI preflight: reject reserved paths and protected metadata before answer import."""
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(pilot, *paths)
    manifest = read_json(_metadata_file(pilot, "pilot_manifest.json"))
    # Scoring during phases requires DEVELOPMENT metadata.
    if manifest["split"] != "DEVELOPMENT" or manifest["protected_evaluation_opened"] is not False:
        raise ValueError("scoring during phases requires DEVELOPMENT metadata")
    selected = _metadata_file(pilot, "selected_pages.jsonl")
    # Reject this input if its file fingerprint no longer matches the recorded version.
    if sha256(selected) != manifest["file_sha256"].get("selected_pages.jsonl"):
        raise ValueError("selected scoring metadata checksum changed")
    return audit_split_metadata(read_jsonl(selected))


# Check institution-family grouping in registered exposed metadata and report any leakage
# risks.
def audit_exposed_design(pilot: Path, evaluation: Path, output: Path) -> dict:
    pages = load_exposed_split_metadata(pilot, evaluation, output)
    result = audit_split_metadata(pages)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "split_audit.json", result)
    write_json(
        output / "manifest.json",
        {
            "schema_version": VERSION,
            "input_sha256": {
                "pilot_manifest": sha256(pilot / "pilot_manifest.json"),
                "selected_metadata": sha256(pilot / "selected_pages.jsonl"),
                "approved_evaluation_manifest": sha256(evaluation / "evaluation_manifest.json"),
            },
            "answers_or_source_text_opened": False,
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(output),
        },
    )
    return {k: v for k, v in result.items() if k != "pages"}
