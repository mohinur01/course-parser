# Prepare repeatable development samples and run the structural parser on them. Keep related
# institution families together and generate predictions before reading review answers.
"""Reproducible DEVELOPMENT sampling and source-only course proposals.

Learning artifacts are separate from the authoritative human review ledger.
The parser never opens course truth; scoring is a later, independent command.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz

from .contextual import VERSION as CONTEXTUAL_VERSION
from .io import (
    artifact_hashes,
    contained_file,
    read_csv,
    read_json,
    read_jsonl,
    read_selected_jsonl,
    sha256,
    verify_files,
    write_csv,
    write_json,
    write_jsonl,
)
from .precision_policy import VERSION as PRECISION_VERSION
from .precision_policy import audit_routes, route_window, write_route_outputs
from .reference import ReferenceProject
from .release import TaskRelease
from .table_structure import layout_conservation

SEED = "course-parser-source-learning-2026-10-06-v1"
RECONCILIATION = "outputs/course_extraction_v3_development_reconciliation_v8/summary.json"
IDENTITIES = (
    "outputs/course_extraction_v3_development_identity_gate_amendment_v5/"
    "development_page_identity_overlay.csv"
)
LOCKED_PILOT = "outputs/course_extraction_v3_ai_pilot_v1/preparation"
DOCUMENTS = "outputs/course_extraction_v3_phase2_dataset_v1_1/development_documents.csv"
VOCABULARY = "outputs/supervisor_keyword_v2/supervisor_keyword_v2.json"
RELEASE = "outputs/supervisor_keyword_v2_page_review_tasks_v1"


# Compute a repeatable hash used to order candidates without depending on their input order.
def _rank(value: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


# Seeded institution round robin; selection never consults course content.
def balanced_sample(items: list[dict], count: int, seed: str) -> list[dict]:
    """Seeded institution round robin; selection never consults course content."""
    groups = defaultdict(list)
    for item in items:
        groups[item["learning_group"]].append(item)
    for group in groups:
        groups[group].sort(key=lambda item: _rank(item["dataset_page_id"], seed))
    order = sorted(groups, key=lambda group: _rank(group, seed))
    result = []
    # Take turns selecting from each institution family until the sample is full or
    # no examples remain.
    while len(result) < count and any(groups.values()):
        for group in order:
            if groups[group] and len(result) < count:
                result.append(groups[group].pop(0))
    return result


# Whole institution/alias families stay together across DEVELOPMENT roles.
def assign_learning_partitions(items: list[dict], seed: str) -> dict[str, str]:
    """Whole institution/alias families stay together across DEVELOPMENT roles."""
    # A quota based on the current cohort would move old training institutions into validation
    # when new families arrive. Hash each family independently.
    return {
        group: "VALIDATION" if int(_rank(group, seed)[:8], 16) % 10 < 3 else "TUNING"
        for group in sorted({item["learning_group"] for item in items})
    }


# Index the approved source catalog, page identities and extraction metadata needed to prepare
# a learning pilot.
def _source_catalog(reference: ReferenceProject) -> tuple[list[dict], dict, dict, dict]:
    root = reference.root
    summary = read_json(root / RECONCILIATION)
    # Stop if the source-review reconciliation has not passed QC.
    if summary.get("qc") != "PASS":
        raise ValueError("source-review reconciliation has not passed QC")
    paths = {}
    for relative, expected in summary["input_hashes"].items():
        path = contained_file(root, relative)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(path) != expected:
            raise ValueError(f"source review snapshot checksum changed: {relative}")
        paths[path.name] = path
    pilot = root / LOCKED_PILOT
    pilot_manifest = read_json(pilot / "preparation_manifest.json")
    # Only the exclusion inventory is opened, not locked images or answers.
    pilot_csv = pilot / "pilot_page_manifest.csv"
    # Stop if the locked-pilot exclusion inventory changed.
    if sha256(pilot_csv) != pilot_manifest["file_sha256"][pilot_csv.name]:
        raise ValueError("locked-pilot exclusion inventory changed")
    identities_path = root / IDENTITIES
    expected_identity = pilot_manifest["source_input_sha256"][str(identities_path)]
    # Stop if the source identity overlay changed.
    if sha256(identities_path) != expected_identity:
        raise ValueError("source identity overlay changed")
    identities = {item["dataset_page_id"]: item for item in read_csv(identities_path)}
    pages = read_jsonl(paths["selected_pages.jsonl"])
    documents = read_csv(root / DOCUMENTS)
    docs = {item["dataset_document_id"]: item for item in documents}
    # Build this sample only from permitted development inputs.
    if any(item["split"] != "DEVELOPMENT" for item in documents + pages):
        raise ValueError("pilot accepts DEVELOPMENT inputs only")
    locked = [item for item in read_csv(pilot_csv) if item["pilot_split"] == "LOCKED_EVALUATION"]
    locked_ids = {item["source_institution_id"] for item in locked}
    locked_checksums = {item["source_pdf_checksum"] for item in locked}
    # Union aliases from the original split and the source-corrected identity.
    parent = {}

    # Find the shared representative for linked aliases so the same institution family stays
    # together.
    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    for page in pages:
        overlay = identities.get(page["dataset_page_id"], {})
        if overlay.get("identity_gate") == "READY_FOR_SOURCE_REVIEW":
            a = find(docs[page["dataset_document_id"]]["institution_family_id"])
            b = find(overlay["source_institution_id"])
            parent[max(a, b)] = min(a, b)
    locked_groups = {find(key) for key in locked_ids}
    eligible = []
    for page in pages:
        overlay = identities.get(page["dataset_page_id"], {})
        if overlay.get("identity_gate") != "READY_FOR_SOURCE_REVIEW":
            continue
        # Stop if the identity overlay and selected source disagree.
        if overlay["source_pdf_checksum"] != page["source_pdf_checksum"]:
            raise ValueError("identity overlay and selected source disagree")
        group = find(overlay["source_institution_id"])
        if group in locked_groups or page["source_pdf_checksum"] in locked_checksums:
            continue
        eligible.append(
            {
                **page,
                "institution": overlay["source_institution"],
                "institution_id": overlay["source_institution_id"],
                "catalog_year": int(overlay["source_catalog_start_year"]),
                "original_reported_identity": {
                    key: page[key] for key in ("institution", "institution_id", "catalog_year")
                },
                "learning_group": group,
                "identity_evidence": overlay["source_evidence"],
            }
        )
    reviews = {
        item["dataset_page_id"]: item
        for item in read_selected_jsonl(
            paths["page_reviews.jsonl"], {item["dataset_page_id"] for item in eligible}
        )
    }
    for page in eligible:
        page["review_available"] = reviews[page["dataset_page_id"]]["complete_course_inventory"]
    provenance = {
        str(path): sha256(path)
        for path in [root / RECONCILIATION, identities_path, pilot_csv, root / DOCUMENTS]
    }
    provenance.update({str(path): sha256(path) for path in paths.values()})
    return eligible, docs, paths, provenance


# Prepare 20 reviewed windows plus a separate seven-hit review queue.
def prepare_learning_pilot(
    reference: ReferenceProject,
    output: Path,
    *,
    seed: str = SEED,
    partition_registry: Path | None = None,
    expand_review: bool = False,
) -> dict:
    """Prepare 20 reviewed windows plus a separate seven-hit review queue."""
    pages, docs, snapshot, inputs = _source_catalog(reference)
    release = TaskRelease.load(reference.root / RELEASE, reference.root / VOCABULARY, reference)
    tasks = {
        (item["source_pdf_checksum"], int(item["central_physical_page"])): item
        for item in release.tasks
    }
    reviewed = [item for item in pages if item["review_available"]]
    hit_pages = [
        item
        for item in reviewed
        if (item["source_pdf_checksum"], item["central_physical_page"]) in tasks
    ]
    selected = []

    # Add a bounded selection of eligible windows to the chosen development role.
    def add(pool, count, role):
        existing = {item["dataset_page_id"] for item in selected}
        chosen = balanced_sample(
            [item for item in pool if item["dataset_page_id"] not in existing], count, seed + role
        )
        selected.extend(
            {**item, "pilot_role": role, "cohort": "REVIEWED_DIAGNOSTIC"} for item in chosen
        )
        return len(chosen)

    reviewed_hits = add(hit_pages, 10, "KEYWORD_HIT")
    random_count = add(
        [x for x in reviewed if x["selection_role"] == "RANDOM_UNBIASED"], 5, "RANDOM"
    )
    challenge_count = add(
        [x for x in reviewed if x["selection_role"] == "STRUCTURAL_CHALLENGE"], 5, "CHALLENGE"
    )
    add(
        [x for x in reviewed if x["selection_role"] == "COURSE_DENSE"],
        20 - len(selected),
        "COURSE_DENSE",
    )
    # Require enough reviewed development page selections to fill the requested diagnostic
    # sample.
    if len(selected) != 20 or random_count != 5 or challenge_count != 5:
        raise ValueError(
            "not enough eligible reviewed DEVELOPMENT windows for the diagnostic pilot"
        )
    if expand_review:
        # Retain the original twenty, then enlarge independent random and hard negative
        # strata. Never use answer contents or parser scores to select.
        add([x for x in reviewed if x["selection_role"] == "RANDOM_UNBIASED"], 10, "RANDOM")
        add([x for x in reviewed if x["selection_role"] == "STRUCTURAL_CHALLENGE"], 10, "CHALLENGE")
        add(
            [x for x in reviewed if x["selection_role"] == "NONCOURSE_CONTROL"],
            10,
            "NONCOURSE_CONTROL",
        )
        add(reviewed, 50 - len(selected), "ADDITIONAL_SOURCE_REVIEW")
        # Require enough reviewed development pages to fill each expanded sampling group.
        if len(selected) != 50:
            raise ValueError("not enough reviewed DEVELOPMENT pages for expanded strata")
    reviewed_count = len(selected)
    # Fill the missing keyword quota from verified DEVELOPMENT documents. These extra windows
    # have blank answer keys until reviewed, and never enter scoring.
    by_checksum = {}
    for item in pages:
        by_checksum.setdefault(item["source_pdf_checksum"], item)
    used = {(item["source_pdf_checksum"], item["central_physical_page"]) for item in selected}
    extra = []
    for key in tasks:
        if key in used or key[0] not in by_checksum:
            continue
        base = by_checksum[key[0]]
        number = key[1]
        page_id = "learning_" + _rank(f"{key[0]}:{number}", seed)[:20]
        doc = docs[base["dataset_document_id"]]
        extra.append(
            {
                **base,
                "dataset_page_id": page_id,
                "central_physical_page": number,
                "context_physical_pages": [
                    x for x in (number - 1, number, number + 1) if 1 <= x <= int(doc["page_count"])
                ],
                "rendered_page_paths": [],
                "geometry_paths": [],
                "selection_role": "KEYWORD_HIT",
                "review_available": False,
                "pilot_role": "KEYWORD_HIT",
                "cohort": "PENDING_KEYWORD_REVIEW",
            }
        )
    selected.extend(balanced_sample(extra, 10 - reviewed_hits, seed + "pending-hits"))
    if expand_review:
        used_ids = {x["dataset_page_id"] for x in selected}
        random_pending = balanced_sample(
            [
                x
                for x in pages
                if not x["review_available"]
                and x["selection_role"] == "RANDOM_UNBIASED"
                and x["dataset_page_id"] not in used_ids
            ],
            10,
            seed + "pending-random",
        )
        selected.extend(
            {**x, "pilot_role": "RANDOM", "cohort": "PENDING_RANDOM_REVIEW"} for x in random_pending
        )
    partitions = assign_learning_partitions(selected, seed)
    if partition_registry is not None:
        registry = read_json(partition_registry)
        # Stop if the partition registry belongs to another development protocol.
        if registry["split"] != "DEVELOPMENT" or registry["seed"] != seed:
            raise ValueError("partition registry belongs to another development protocol")
        # Reject unknown role in partition registry.
        if set(registry["families"].values()) - {"TUNING", "VALIDATION"}:
            raise ValueError("unknown role in partition registry")
        partitions.update(
            {group: role for group, role in registry["families"].items() if group in partitions}
        )
        inputs[str(partition_registry.resolve())] = sha256(partition_registry)
    for item in selected:
        item["learning_partition"] = partitions[item["learning_group"]]
        task = tasks.get((item["source_pdf_checksum"], item["central_physical_page"]))
        item["original_page_task_id"] = task["page_task_id"] if task else None
        item["original_hit_count"] = int(task["hit_count"]) if task else 0
    # Verify original PDFs and frozen streams before creating a release.
    source_hashes = {}
    streams = {}
    for item in selected:
        doc = docs[item["dataset_document_id"]]
        for path, expected in (
            (Path(item["source_pdf_path"]), item["source_pdf_checksum"]),
            (Path(item["page_stream_path"]), doc["page_stream_sha256"]),
        ):
            if str(path) not in source_hashes:
                # Reject this input if its file fingerprint no longer matches the recorded
                # version.
                if sha256(path) != expected:
                    raise ValueError(f"source input checksum changed: {path}")
                source_hashes[str(path)] = expected
        stream_path = item["page_stream_path"]
        if stream_path not in streams:
            with gzip.open(stream_path, "rt", encoding="utf-8") as stream:
                records = [json.loads(line) for line in stream if line.strip()]
            # The recorded text stream must belong to the same source PDF.
            if any(x["source_pdf_checksum"] != item["source_pdf_checksum"] for x in records):
                raise ValueError("frozen stream points to another PDF")
            streams[stream_path] = {int(x["page_number"]): x for x in records}
            # Stop if the frozen stream contains duplicate physical pages.
            if len(streams[stream_path]) != len(records):
                raise ValueError("frozen stream contains duplicate physical pages")
        # Stop if the context page is missing from frozen stream.
        if not set(item["context_physical_pages"]).issubset(streams[stream_path]):
            raise ValueError("context page is missing from frozen stream")
    script = reference.root / "scripts/course_extraction_v3_phase2/select_and_prepare_pages.py"
    spec = importlib.util.spec_from_file_location("course_learning_source_geometry", script)
    geometry_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geometry_module)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original_hits = []
    templates = []
    for item in selected:
        packet = output / "packets" / item["dataset_page_id"]
        packet.mkdir(parents=True)
        artifacts = []
        with fitz.open(item["source_pdf_path"]) as pdf:
            for number in item["context_physical_pages"]:
                page = pdf[number - 1]
                image = packet / f"page_{number:05d}.png"
                page.get_pixmap(dpi=100, alpha=False).save(image)
                geometry = packet / f"page_{number:05d}.embedded_geometry.json"
                write_json(geometry, geometry_module.embedded_geometry(page))
                text = packet / f"page_{number:05d}.source_text.json"
                write_json(text, streams[item["page_stream_path"]][number])
                artifacts.append(
                    {
                        "physical_page": number,
                        "image_path": str(image),
                        "image_sha256": sha256(image),
                        "embedded_geometry_path": str(geometry),
                        "embedded_geometry_sha256": sha256(geometry),
                        "source_text_path": str(text),
                        "source_text_sha256": sha256(text),
                    }
                )
        item["packet_path"] = str(packet.relative_to(output))
        write_json(
            packet / "source_reference.json",
            {
                "selected_page": item,
                "page_artifacts": artifacts,
                "predictions_created": False,
                "source_truth_assessed": False,
                "render_dpi": 100,
                "evidence_scope": "PAGE_WINDOW_NOT_COMPLETE_CATALOG",
            },
        )
        task_id = item["original_page_task_id"]
        for hit in release.hits.get(task_id, []):
            for span in json.loads(hit["source_spans_json"]):
                number = span["page_index"] + 1
                raw = streams[item["page_stream_path"]][number]["raw_text"]
                # Stop if the original keyword span is outside frozen text.
                if not 0 <= span["raw_start"] < span["raw_end"] <= len(raw):
                    raise ValueError("original keyword span is outside frozen text")
            original_hits.append({"dataset_page_id": item["dataset_page_id"], "original_hit": hit})
        templates.append(
            {
                "dataset_page_id": item["dataset_page_id"],
                "status": "NOT_REVIEWED",
                "complete_course_inventory": False,
                "source_visible_course_count": None,
                "gold_course_ids": [],
                "context_only_gold_course_ids": [],
                "reviewer_id": None,
                "second_reviewer_id": None,
                "adjudicator_id": None,
                "context_only_course_count": 0,
                "source_channels_reviewed": [],
                "notes": None,
            }
        )
    write_jsonl(output / "selected_pages.jsonl", selected)
    write_jsonl(output / "original_hits.jsonl", original_hits)
    write_jsonl(output / "page_review_template.jsonl", templates)
    # Typed CourseTruth records are accepted by score-learning; never fill them from model
    # proposals. An empty file deliberately cannot masquerade as gold.
    write_jsonl(output / "course_truth_template.jsonl", [])
    annotation = importlib.import_module("catalog_mining.course_extraction_v3.annotation")
    write_json(
        output / "review_schema.json",
        {
            "page_review": annotation.PageReview.model_json_schema(),
            "course_truth": annotation.CourseTruth.model_json_schema(),
            "instructions": (
                "Inspect central images first; inventory every independent course. "
                "Record source-quoted fields with manual image evidence and exact PDF identity. "
                "Use neighboring pages only for boundaries and continuations. "
                "If a course extends outside this window, request more context "
                "and retain unresolved status. "
                "A model proposal is never an answer key."
            ),
        },
    )
    write_csv(
        output / "hit_disposition_template.csv",
        [
            {
                "hit_id": x["original_hit"]["hit_id"],
                "page_task_id": x["original_hit"]["page_task_id"],
                "decision": "",
                "owner_course_id": "",
                "keyword_field": "",
            }
            for x in original_hits
        ],
        ["hit_id", "page_task_id", "decision", "owner_course_id", "keyword_field"],
    )
    manifest = {
        "schema_version": "course-parser-learning-pilot-v1",
        "status": "READY",
        "seed": seed,
        "split": "DEVELOPMENT",
        "protected_evaluation_opened": False,
        "reviewed_diagnostic_windows": reviewed_count,
        "reviewed_keyword_windows": reviewed_hits,
        "pending_keyword_windows": sum(x["cohort"] == "PENDING_KEYWORD_REVIEW" for x in selected),
        "desired_keyword_windows": 10,
        "pending_random_windows": sum(x["cohort"] == "PENDING_RANDOM_REVIEW" for x in selected),
        "expanded_review_selection": expand_review,
        "original_physical_hits": len(original_hits),
        "selection_policy": (
            "Seeded family round robin. RANDOM pages were originally selected independently "
            "of keywords; complete-review availability conditions this diagnostic cohort."
        ),
        "role_counts": dict(Counter(x["pilot_role"] for x in selected)),
        "learning_partitions": partitions,
        "source_inputs": source_hashes,
        "metadata_inputs": inputs,
        "snapshot_paths": {k: str(v) for k, v in snapshot.items()},
        "vocabulary_path": str(reference.root / VOCABULARY),
        "vocabulary_sha256": sha256(reference.root / VOCABULARY),
        "reference_code_sha256": reference.provenance(),
        "geometry_producer_sha256": sha256(script),
        "truth_imported": False,
        "truth_quality": (
            "Existing exposed DEVELOPMENT annotations; independent adjudication is not assumed."
        ),
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "pilot_manifest.json", manifest)
    return {
        key: manifest[key]
        for key in (
            "status",
            "reviewed_diagnostic_windows",
            "reviewed_keyword_windows",
            "pending_keyword_windows",
            "original_physical_hits",
            "role_counts",
        )
    }


# Check the sample's saved files and source identities before it is used for extraction or
# evaluation.
def verify_learning_pilot(run: Path, *, recheck_inputs: bool = True) -> dict:
    from .evaluation_design import audit_split_metadata
    from .omission_review import _reject_reserved_paths

    _reject_reserved_paths(run, run / "pilot_manifest.json")
    manifest = read_json(run / "pilot_manifest.json")
    # Reject a learning sample that is not marked as development data.
    if (
        manifest["schema_version"] != "course-parser-learning-pilot-v1"
        or manifest["split"] != "DEVELOPMENT"
    ):
        raise ValueError("not a DEVELOPMENT learning pilot")
    # Stop if the pilot policy changed.
    if (
        manifest["protected_evaluation_opened"] is not False
        or manifest["truth_imported"] is not False
    ):
        raise ValueError("pilot policy changed")
    verify_files(run, manifest["file_sha256"])
    # When requested, recheck the original inputs as well as the saved sample files.
    if recheck_inputs:
        for path, expected in {**manifest["source_inputs"], **manifest["metadata_inputs"]}.items():
            # Reject this input if its file fingerprint no longer matches the recorded
            # version.
            if sha256(Path(path)) != expected:
                raise ValueError(f"source input checksum changed: {path}")
    pages = read_jsonl(run / "selected_pages.jsonl")
    split_audit = audit_split_metadata(pages)
    # Reject duplicate pilot page.
    if len({x["dataset_page_id"] for x in pages}) != len(pages):
        raise ValueError("duplicate pilot page")
    groups = defaultdict(set)
    checksums = defaultdict(set)
    physical = set()
    for item in pages:
        # Keep protected pages out of the development sample.
        if item["split"] != "DEVELOPMENT":
            raise ValueError("protected page entered pilot")
        groups[item["learning_group"]].add(item["learning_partition"])
        checksums[item["source_pdf_checksum"]].add(item["learning_partition"])
        key = (item["source_pdf_checksum"], item["central_physical_page"])
        # Reject duplicate physical central page.
        if key in physical:
            raise ValueError("duplicate physical central page")
        physical.add(key)
        packet = contained_file(run, item["packet_path"])
        source = read_json(packet / "source_reference.json")
        # Stop if the packet metadata differs from pilot selection.
        if source["selected_page"] != item:
            raise ValueError("packet metadata differs from pilot selection")
        # Stop if the source packet does not cover the declared context.
        if {x["physical_page"] for x in source["page_artifacts"]} != set(
            item["context_physical_pages"]
        ):
            raise ValueError("source packet does not cover the declared context")
        for asset in source["page_artifacts"]:
            for key in ("image", "embedded_geometry", "source_text"):
                path = Path(asset[key + "_path"]).resolve()
                # Reject a path or coordinate that falls outside its allowed source boundary.
                if (
                    not path.is_relative_to(packet.resolve())
                    or sha256(path) != asset[key + "_sha256"]
                ):
                    raise ValueError("source artifact escapes packet or changed checksum")
        # When requested, recheck the original inputs as well as the saved sample files.
        if recheck_inputs:
            with fitz.open(item["source_pdf_path"]) as pdf:
                for asset in source["page_artifacts"]:
                    page = pdf[asset["physical_page"] - 1]
                    archived = fitz.Pixmap(asset["image_path"])
                    fresh = page.get_pixmap(dpi=100, alpha=False)
                    # Stop if the page image differs from verified source PDF.
                    if (archived.width, archived.height, archived.n, archived.samples) != (
                        fresh.width,
                        fresh.height,
                        fresh.n,
                        fresh.samples,
                    ):
                        raise ValueError("page image differs from verified source PDF")
    # Keep each institution family in a single learning partition.
    if any(len(partitions) != 1 for partitions in groups.values()):
        raise ValueError("institution family crosses learning partitions")
    # Keep all pages from one source PDF in the same learning partition.
    if any(len(partitions) != 1 for partitions in checksums.values()):
        raise ValueError("source PDF crosses learning partitions")
    hits = read_jsonl(run / "original_hits.jsonl")
    # Count each original keyword hit only once.
    if len({x["original_hit"]["hit_id"] for x in hits}) != len(hits):
        raise ValueError("original hit counted more than once")
    # Stop if the original hit accounting changed.
    if len(hits) != manifest["original_physical_hits"]:
        raise ValueError("original hit accounting changed")
    page_by_id = {x["dataset_page_id"]: x for x in pages}
    hit_counts = Counter()
    for wrapper in hits:
        item = page_by_id[wrapper["dataset_page_id"]]
        hit = wrapper["original_hit"]
        # Stop if the original hit belongs to another page task.
        if (
            hit["page_task_id"] != item["original_page_task_id"]
            or int(hit["physical_page"]) != item["central_physical_page"]
        ):
            raise ValueError("original hit belongs to another page task")
        hit_counts[item["dataset_page_id"]] += 1
    # Stop if the original per-page hit accounting changed.
    if any(hit_counts[x["dataset_page_id"]] != x["original_hit_count"] for x in pages):
        raise ValueError("original per-page hit accounting changed")
    return {
        "status": "PASS",
        "windows": len(pages),
        "original_physical_hits": len(hits),
        "evaluation_role_counts": split_audit["role_counts"],
        "overlapping_context_window_pairs": split_audit["overlapping_context_window_pairs"],
        "generalization_claim_permitted": False,
    }


# Run the existing structural v3 stages without reading annotations.
def run_learning_baseline(
    reference: ReferenceProject,
    pilot: Path,
    output: Path,
    *,
    repair_prose_columns: bool = False,
    contextual_course_assembly: bool = False,
    ordering_engine: str | None = None,
    engine_exports: Path | None = None,
    transition_constraints: bool = True,
) -> dict:
    """Run the existing structural v3 stages without reading annotations."""
    if ordering_engine is None:
        ordering_engine = "source_rows" if contextual_course_assembly else "legacy"
    # Reject unknown ordering engine.
    if ordering_engine not in {"legacy", "source_rows", "docling", "ppstructure"}:
        raise ValueError("unknown ordering engine")
    # Use the same course-ownership stages when comparing reading-order methods.
    if ordering_engine != "legacy" and not contextual_course_assembly:
        raise ValueError("ordering experiments require the same contextual ownership stages")
    verify_learning_pilot(pilot)
    manifest = read_json(pilot / "pilot_manifest.json")
    if engine_exports is not None:
        external_manifest = read_json(engine_exports / "manifest.json")
        # The external layout exports must belong to the sample being processed.
        if external_manifest.get("pilot_manifest_sha256") != sha256(pilot / "pilot_manifest.json"):
            raise ValueError("external engine exports identify a different pilot")
    # Stop if the reference parser changed after pilot preparation; prepare a new version.
    if reference.provenance() != manifest["reference_code_sha256"]:
        raise ValueError("reference parser changed after pilot preparation; prepare a new version")
    vocabulary_path = Path(manifest["vocabulary_path"])
    # Stop if the frozen vocabulary changed.
    if sha256(vocabulary_path) != manifest["vocabulary_sha256"]:
        raise ValueError("frozen vocabulary changed")
    vocabulary = reference.vocabulary(vocabulary_path)
    modules = {
        name: importlib.import_module("catalog_mining.course_extraction_v3." + name)
        for name in (
            "layout_baseline",
            "layout",
            "document_grammar",
            "header_detection",
            "entry_classifier",
            "block_builder",
            "continuation_resolver",
            "field_parser",
        )
    }
    engine = modules["layout_baseline"].PyMuPDFGeometryLayoutEngine()
    profiler = modules["document_grammar"].DeterministicDocumentGrammarProfiler()
    detector = modules["header_detection"].GrammarHeaderDetector()
    classifier = modules["entry_classifier"].StructuralEntryClassifier()
    builder = modules["block_builder"].StructuralCourseBlockBuilder()
    continuation = modules["continuation_resolver"].StructuralContinuationResolver()
    fields = modules["field_parser"].StructuralCourseFieldParser()
    pages = read_jsonl(pilot / "selected_pages.jsonl")
    output.mkdir(parents=True, exist_ok=False)
    proposals, records, failures = [], [], []
    # Process each selected page with its recorded neighboring pages and keep its failure
    # independent of the other selections.
    for selected in pages:
        page_id = selected["dataset_page_id"]
        packet = contained_file(pilot, selected["packet_path"])
        source_layouts = ()
        try:
            # Read the native page evidence first. Optional layout tools can propose a
            # different order without replacing this original text.
            native_pages = []
            for number in selected["context_physical_pages"]:
                native_pages.append(
                    engine.analyze_packet(
                        packet, physical_page=number, dataset_page_id=f"{page_id}:p{number}"
                    )
                )
                source_layouts = tuple(native_pages)
            layouts = source_layouts
            # Use nearby text and typography for heading detection, course decisions, text
            # ownership and field parsing.
            if contextual_course_assembly:
                from .contextual import (
                    ContextualContinuationResolver,
                    ContextualCourseFieldParser,
                    ContextualEntryClassifier,
                    ContextualHeaderDetector,
                    contextual_layout,
                    packet_styles,
                )

                styles = packet_styles(packet, layouts)
                layouts = tuple(
                    contextual_layout(
                        x,
                        modules["layout"],
                        reference.models,
                        ordering="source_rows" if ordering_engine == "source_rows" else "legacy",
                        styles=styles,
                    )
                    for x in layouts
                )
                # An external reading order is an optional comparison path and needs exports
                # verified against this same sample.
                if ordering_engine in {"docling", "ppstructure"}:
                    from .engine_order import external_order

                    # External ordering requires verified engine exports.
                    if engine_exports is None:
                        raise ValueError("external ordering requires checksum-bound engine exports")
                    layouts = tuple(
                        external_order(
                            x, selected, packet, engine_exports, ordering_engine, modules["layout"]
                        )
                        for x in layouts
                    )
                styles = packet_styles(packet, layouts)
                detector = ContextualHeaderDetector(reference.models, styles)
                from .table_structure import table_contexts

                classifier = ContextualEntryClassifier(
                    reference.models, styles, table_contexts=table_contexts(layouts)
                )
                fields = ContextualCourseFieldParser(
                    reference.models, styles, transition_constraints=transition_constraints
                )
                continuation = ContextualContinuationResolver(
                    reference.models, transition_constraints=transition_constraints, styles=styles
                )
                from .ownership import (
                    OwnedCourseBlockBuilder,
                    audit_owned_blocks,
                    course_field_links,
                )

                builder = OwnedCourseBlockBuilder(reference.models)
            elif repair_prose_columns:
                from .learning_layout import repair_parallel_prose

                layouts = tuple(
                    repair_parallel_prose(x, modules["layout"], reference.models) for x in layouts
                )
            graph = reference.models.DocumentGraph(
                identity=reference.models.SourceDocumentIdentity(
                    source_document_id=selected["source_document_id"],
                    source_pdf_checksum=selected["source_pdf_checksum"],
                    source_pdf_path=selected["source_pdf_path"],
                    institution_id=selected["institution_id"],
                    institution_name=selected["institution"],
                    catalog_year=str(selected["catalog_year"]),
                ),
                pages=tuple(modules["layout"].proposal_to_page_graph(x) for x in layouts),
            )
            profile = profiler.profile(graph)
            candidates = detector.detect(graph, profile)
            decisions = classifier.classify(graph, profile, candidates)
            blocks = builder.build(graph, profile, candidates, decisions)
            blocks = continuation.resolve(graph, profile, candidates, decisions, blocks)
            # Use nearby text and typography for heading detection, course decisions, text
            # ownership and field parsing.
            if contextual_course_assembly:
                audit_owned_blocks(graph, blocks)
            courses = fields.parse(graph, profile, candidates, blocks)
            field_links = (
                course_field_links(graph, blocks, courses) if contextual_course_assembly else []
            )
            from .page_accounting import build_page_accounting

            source_graph = reference.models.DocumentGraph(
                identity=graph.identity,
                pages=tuple(modules["layout"].proposal_to_page_graph(x) for x in source_layouts),
            )
            accounting = build_page_accounting(
                graph,
                blocks,
                courses,
                source_graph=source_graph,
                candidates=candidates,
                decisions=decisions,
                table_contexts=table_contexts(layouts) if contextual_course_assembly else {},
            )
            accounting["context_inventory_status"] = "COMPLETE_EXTRACTED_WINDOW"
            accounting["unavailable_context_pages"] = []
            by_candidate = {x.candidate_id: x for x in candidates}
            window_proposal = {
                "dataset_page_id": page_id,
                "status": "PROPOSED",
                "source_truth_read": False,
                "profile": profile.model_dump(mode="json"),
                "layouts": [x.model_dump(mode="json") for x in layouts],
                "source_layouts": [x.model_dump(mode="json") for x in source_layouts],
                "layout_interpretations": [
                    layout_conservation(a, b) for a, b in zip(source_layouts, layouts, strict=True)
                ],
                "table_context_proposals": table_contexts(layouts)
                if contextual_course_assembly
                else {},
                "candidates": [x.model_dump(mode="json") for x in candidates],
                "decisions": [x.model_dump(mode="json") for x in decisions],
                "blocks": [x.model_dump(mode="json") for x in blocks],
                "boundary_proposals": getattr(continuation, "boundary_proposals", []),
                "continuation_proposals": getattr(continuation, "continuation_proposals", []),
                "field_sequence_proposals": getattr(fields, "sequence_proposals", []),
                "header_ownership_conflicts": getattr(detector, "ownership_conflicts", []),
                "page_pattern_proposals": getattr(detector, "page_pattern_proposals", []),
                "course_field_links": field_links,
                "whole_page_accounting": accounting,
                "central_candidate_count": sum(
                    x.physical_page == selected["central_physical_page"] for x in candidates
                ),
            }
            window_records = []
            for course in courses:
                header_page = by_candidate[course.header_candidate_id].physical_page
                matches = []
                for name in ("title", "description"):
                    field = getattr(course, name)
                    if field.raw:
                        matches.extend(
                            {
                                **match,
                                "field": name.upper(),
                                "field_status": str(field.status),
                                "ownership_status": "MODEL_PROPOSAL",
                            }
                            for match in reference.matcher.match_text(field.raw, vocabulary)
                        )
                window_records.append(
                    {
                        "dataset_page_id": page_id,
                        "header_physical_page": header_page,
                        "scope": "CENTRAL"
                        if header_page == selected["central_physical_page"]
                        else "CONTEXT",
                        "course": course.model_dump(mode="json"),
                        "keyword_matches": matches,
                    }
                )
            routes = route_window(selected, window_proposal, window_records, accounting)
            audit_routes(selected, window_proposal, window_records, accounting, routes)
            window_proposal["precision_routes"] = routes
            proposals.append(window_proposal)
            records.extend(window_records)
        except (ValueError, KeyError, RuntimeError) as exc:
            from .page_accounting import VERSION as ACCOUNTING_VERSION
            from .page_accounting import build_page_accounting

            # A failed downstream stage must not erase the available native inventory.
            accounting = {
                "schema_version": ACCOUNTING_VERSION,
                "status": "ACCOUNTING_UNAVAILABLE",
                "reason": "NO_VALID_NATIVE_GRAPH_AVAILABLE",
                "rows": None,
                "source_truth_read": False,
                "semantic_truth_assigned": False,
                "source_extraction_completeness_verified": False,
            }
            if source_layouts:
                try:
                    source_graph = reference.models.DocumentGraph(
                        identity=reference.models.SourceDocumentIdentity(
                            source_document_id=selected["source_document_id"],
                            source_pdf_checksum=selected["source_pdf_checksum"],
                            source_pdf_path=selected["source_pdf_path"],
                        ),
                        pages=tuple(
                            modules["layout"].proposal_to_page_graph(x) for x in source_layouts
                        ),
                    )
                    accounting = build_page_accounting(source_graph, (), ())
                    accounting["failure_fallback_source_only"] = True
                except (ValueError, KeyError, RuntimeError) as accounting_error:
                    accounting["error"] = str(accounting_error)
            unavailable = sorted(
                set(selected["context_physical_pages"]) - {x.physical_page for x in source_layouts}
            )
            accounting["context_inventory_status"] = (
                "PARTIAL_OR_UNAVAILABLE" if unavailable else "COMPLETE_EXTRACTED_WINDOW"
            )
            accounting["unavailable_context_pages"] = unavailable
            failures.append(
                {"dataset_page_id": page_id, "status": "PARSER_STAGE_FAILED", "error": str(exc)}
            )
            proposals.append(
                {
                    "dataset_page_id": page_id,
                    "status": "PARSER_STAGE_FAILED",
                    "source_truth_read": False,
                    "source_layouts": [x.model_dump(mode="json") for x in source_layouts],
                    "whole_page_accounting": accounting,
                }
            )
    write_jsonl(output / "page_proposals.jsonl", proposals)
    write_jsonl(output / "course_proposals.jsonl", records)
    write_jsonl(output / "stage_failures.jsonl", failures)
    write_route_outputs(
        output, [r for p in proposals for r in p.get("precision_routes", [])], failures
    )
    write_jsonl(
        output / "page_accounting.jsonl",
        [
            {
                "dataset_page_id": p["dataset_page_id"],
                "parser_stage_status": p["status"],
                "whole_page_accounting": p["whole_page_accounting"],
            }
            for p in proposals
        ],
    )
    result = {
        "schema_version": "course-parser-learning-predictions-v1",
        "status": "PROPOSALS_PARTIAL" if failures else "PROPOSALS_READY",
        "pilot_path": str(pilot.resolve()),
        "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        "source_truth_read": False,
        "protected_evaluation_opened": False,
        "parser_version": CONTEXTUAL_VERSION
        if contextual_course_assembly
        else "structural-v3-window-prose-columns-v1"
        if repair_prose_columns
        else "structural-v3-window-baseline-v1",
        "experimental_prose_column_repair": repair_prose_columns,
        "contextual_course_assembly": contextual_course_assembly,
        "transition_constraints": contextual_course_assembly and transition_constraints,
        "ordering_engine": ordering_engine,
        "reading_order_policy": "SOURCE_ROWS_PRIMARY_EXTERNAL_REPLACEMENT_EXPLICIT_EXPERIMENT_ONLY",
        "automatic_engine_order_selection": False,
        "whole_page_accounting_required": True,
        "precision_routing_required": True,
        "precision_policy_version": PRECISION_VERSION,
        "automatic_publication_authorized": False,
        "engine_exports_manifest_sha256": sha256(engine_exports / "manifest.json")
        if engine_exports
        else None,
        "page_windows": len(pages),
        "central_course_proposals": sum(x["scope"] == "CENTRAL" for x in records),
        "failed_windows": len(failures),
        "stages": {
            name: getattr(stage, "version", getattr(stage, "engine_version", None))
            for name, stage in (
                ("layout", engine),
                ("profile", profiler),
                ("header", detector),
                ("entry", classifier),
                ("block", builder),
                ("continuation", continuation),
                ("field", fields),
            )
        },
        "reference_code_sha256": reference.provenance(),
        "software": {"python": sys.version, "pymupdf": fitz.VersionBind},
        "workspace_code_sha256": {
            str(p): sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))
        },
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "prediction_manifest.json", result)
    return {
        key: result[key]
        for key in (
            "status",
            "page_windows",
            "central_course_proposals",
            "failed_windows",
            "whole_page_accounting_required",
        )
    }
