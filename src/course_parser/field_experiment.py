# Prepare the same text, coordinates and page images for a future comparison of field
# classifiers. Leave annotation answers blank and check that competing methods would use
# equivalent inputs.
"""Source-only preparation for a narrow, deferred multimodal field experiment.

No model runtime, training, inference, label scoring or course acceptance lives here.
The same exact source spans and page frames are shared by all prospective arms.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from pathlib import Path

import fitz

from .io import (
    artifact_hashes,
    contained_file,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_json,
    write_jsonl,
)
from .omission_review import _reject_reserved_paths
from .sequence_annotations import LABELS, audit_sequence_reviews, source_order_digest

VERSION = "source-bound-field-experiment-v1"
ARMS = ("TEXT_GEOMETRY_TYPOGRAPHY", "LAYOUTLMV3_NO_IMAGE", "LAYOUTLMV3_IMAGE")
FOCUS_ROLES = ("TITLE", "DESCRIPTION", "PREREQUISITES", "NONCOURSE")


# Transform canonical PDF points to 0..1000; never silently clamp bad evidence.
def normalized_box(geometry: dict, width: float, height: float) -> list[int]:
    """Transform canonical PDF points to 0..1000; never silently clamp bad evidence."""
    values = [geometry[k] for k in ("x0", "y0", "x1", "y1")]
    # Coordinates must be finite numbers so they can be mapped back to the source.
    if not all(math.isfinite(v) for v in [width, height, *values]):
        raise ValueError("nonfinite source geometry")
    x0, y0, x1, y1 = values
    # Every source box needs positive area and must fit inside the original page.
    if not (width > 0 and height > 0 and 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("source geometry outside canonical page or empty")
    return [
        math.floor(1000 * x0 / width),
        math.floor(1000 * y0 / height),
        math.ceil(1000 * x1 / width),
        math.ceil(1000 * y1 / height),
    ]


# Whitespace tokenization preserves original case, punctuation and node offsets.
def source_words(unit: dict) -> list[dict]:
    """Whitespace tokenization preserves original case, punctuation and node offsets."""
    return [
        {"text_span": {"start": m.start(), "end": m.end()}, "raw_text": m.group()}
        for m in re.finditer(r"\S+", unit["raw_text"])
    ]


# Do not guess a label when a reviewed field boundary cuts through a word.
def project_word_labels(words: list[dict], spans: list[dict]) -> list[str | None]:
    """Do not guess a label when a reviewed field boundary cuts through a word."""
    labels = []
    for word in words:
        a, b = word["text_span"]["start"], word["text_span"]["end"]
        covering = [s for s in spans if s["text_span"]["start"] <= a < b <= s["text_span"]["end"]]
        labels.append(covering[0]["label"] if len(covering) == 1 else None)
    return labels


# Relative geometry and native emphasis; unavailable typography stays explicit.
def unit_features(unit: dict, geometry: dict) -> dict:
    """Relative geometry and native emphasis; unavailable typography stays explicit."""
    width, height = geometry["canonical_width_points"], geometry["canonical_height_points"]
    anchors = [a for a in unit["evidence"] if a.get("geometry")]
    result = {
        "bbox_1000": None,
        "geometry_status": "UNAVAILABLE",
        "typography_status": "UNAVAILABLE",
        "font_size_relative": None,
        "bold_fraction": None,
        "italic_fraction": None,
    }
    if len(anchors) == 1:
        box = anchors[0]["geometry"]
        if box["coordinate_system"] == "PDF_POINTS_CANONICAL":
            try:
                result["bbox_1000"] = normalized_box(box, width, height)
                result["geometry_status"] = "SOURCE_BOUND_SEGMENT_BOX"
            except ValueError:
                result["geometry_status"] = "INVALID_SOURCE_GEOMETRY"
    # Repeating a proven segment box for its words is transparent; these are not invented
    # precise word boxes. Never match typography by normalized/fuzzy text.
    matches = [
        line
        for line in geometry["lines"]
        if unit["node_id"].endswith(":" + line["line_id"]) and line["text"] == unit["raw_text"]
    ]
    sizes = [
        s["size"]
        for line in geometry["lines"]
        for s in line["spans"]
        if s["text"].strip() and math.isfinite(s["size"]) and s["size"] > 0
    ]
    if len(matches) == 1 and sizes:
        spans = matches[0]["spans"]
        if "".join(s["text"] for s in spans) == unit["raw_text"]:
            measured = [s for s in spans if s["text"].strip()]
            weight = sum(len(s["text"]) for s in measured)
            if weight and all(math.isfinite(s["size"]) and s["size"] > 0 for s in measured):
                result.update(
                    typography_status="EXACT_NATIVE_LINE",
                    font_size_relative=sum(s["size"] * len(s["text"]) for s in measured)
                    / weight
                    / statistics.median(sizes),
                    bold_fraction=sum(len(s["text"]) for s in measured if s["flags"] & 16) / weight,
                    italic_fraction=sum(len(s["text"]) for s in measured if s["flags"] & 2)
                    / weight,
                )
    return result


# Resolve an existing packet file only within the approved packet directory.
def _packet_file(packet: Path, value: str) -> Path:
    _reject_reserved_paths(value)
    path = Path(value).resolve()
    # Reject a path or coordinate that falls outside its allowed source boundary.
    if not path.is_relative_to(packet.resolve()):
        raise ValueError("field input escapes its source packet")
    return contained_file(packet, str(path.relative_to(packet.resolve())))


# Resolve a packet file and require its contents to match the original frozen artifact
# checksum.
def _frozen_packet_file(packet: Path, pilot: Path, manifest: dict, value: str) -> Path:
    path = _packet_file(packet, value)
    relative = str(path.relative_to(pilot.resolve()))
    # Reject this input if its file fingerprint no longer matches the recorded version.
    if sha256(path) != manifest["file_sha256"].get(relative):
        raise ValueError("frozen field source file checksum changed")
    return path


# Write matching text, geometry and image inputs for the deferred field-classifier experiment.
def prepare_field_experiment(templates: Path, selected: Path, output: Path) -> dict:
    _reject_reserved_paths(templates, selected, output)
    for name in ("manifest.json", "sequence_template.jsonl"):
        _packet_file(templates, str(templates / name))
    readiness = audit_sequence_reviews(templates, selected)
    reviewed_rows = read_jsonl(selected)
    # Require at least one permitted page before preparing the experiment.
    if not reviewed_rows:
        raise ValueError("empty field experiment selection")
    originals = {r["dataset_page_id"]: r for r in read_jsonl(templates / "sequence_template.jsonl")}
    template_manifest = read_json(templates / "manifest.json")
    eligible = {
        r["dataset_page_id"] for r in readiness["window_readiness"] if r["training_eligible"]
    }
    output.mkdir(parents=True, exist_ok=False)
    windows, frames, counts = [], [], Counter()
    for reviewed in reviewed_rows:
        # Inputs remain identical when humans split a mixed-field node for labels.
        row = originals[reviewed["dataset_page_id"]]
        packet = Path(row["source_packet_path"])
        _reject_reserved_paths(packet)
        pilot = packet.parent.parent
        _reject_reserved_paths(pilot)
        pilot_manifest_path = _packet_file(pilot, str(pilot / "pilot_manifest.json"))
        # Stop if the field experiment frozen source pilot differs.
        if (
            packet.parent.name != "packets"
            or sha256(pilot_manifest_path) != template_manifest["pilot_manifest_sha256"]
        ):
            raise ValueError("field experiment frozen source pilot differs")
        pilot_manifest = read_json(pilot_manifest_path)

        ref = read_json(
            _frozen_packet_file(
                packet, pilot, pilot_manifest, str(packet / "source_reference.json")
            )
        )
        identity = ref["selected_page"]
        # Stop if the field experiment source packet identity changed.
        if any(
            identity[k] != row[k]
            for k in (
                "dataset_page_id",
                "source_document_id",
                "source_pdf_checksum",
                "learning_group",
                "learning_partition",
                "central_physical_page",
                "context_physical_pages",
            )
        ):
            raise ValueError("field experiment source packet identity changed")
        # Prepare field-experiment inputs only from permitted tuning sources.
        if identity["split"] != "DEVELOPMENT" or row["learning_partition"] != "TUNING":
            raise ValueError("field preparation accepts TUNING DEVELOPMENT source only")
        geometries = {}
        for artifact in ref["page_artifacts"]:
            image = _frozen_packet_file(packet, pilot, pilot_manifest, artifact["image_path"])
            native = _frozen_packet_file(
                packet, pilot, pilot_manifest, artifact["embedded_geometry_path"]
            )
            # Reject this input if its file fingerprint no longer matches the recorded
            # version.
            if (
                sha256(image) != artifact["image_sha256"]
                or sha256(native) != artifact["embedded_geometry_sha256"]
            ):
                raise ValueError("field source image or geometry checksum changed")
            geometry = read_json(native)
            page = artifact["physical_page"]
            # The field must refer to the same physical page as its source metadata.
            if geometry["physical_page"] != page or page not in row["context_physical_pages"]:
                raise ValueError("field source physical page differs")
            # Field source requires canonical geometry.
            if (
                geometry["coordinate_system"]
                != "PDF_POINTS_NATIVE_AND_ROTATION_NORMALIZED_CANONICAL"
            ):
                raise ValueError("field source requires canonical geometry")
            pixmap = fitz.Pixmap(str(image))
            width, height = geometry["canonical_width_points"], geometry["canonical_height_points"]
            # Reject invalid source page dimensions.
            if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
                raise ValueError("invalid source page dimensions")
            # Rendering rounds dimensions. Large aspect mismatch is not repaired.
            # Check for source image and canonical geometry aspect ratio differ before
            # proceeding.
            if abs(pixmap.width / pixmap.height - width / height) > 0.01:
                raise ValueError("source image and canonical geometry aspect ratio differ")
            geometries[page] = geometry
            frames.append(
                {
                    "dataset_page_id": row["dataset_page_id"],
                    "physical_page": page,
                    "image_path": str(image),
                    "image_sha256": artifact["image_sha256"],
                    "geometry_sha256": artifact["embedded_geometry_sha256"],
                    "canonical_width_points": width,
                    "canonical_height_points": height,
                    "image_width_pixels": pixmap.width,
                    "image_height_pixels": pixmap.height,
                    "canonical_to_image": [
                        pixmap.width / width,
                        0,
                        0,
                        pixmap.height / height,
                        0,
                        0,
                    ],
                    "canonical_to_1000": [1000 / width, 0, 0, 1000 / height, 0, 0],
                }
            )
        # Reject missing source context page frame.
        if set(geometries) != set(row["context_physical_pages"]):
            raise ValueError("missing source context page frame")
        units = []
        for unit in row["units"]:
            # Stop if the field unit evidence belongs to another source.
            if any(
                a["source_pdf_checksum"] != row["source_pdf_checksum"]
                or a["source_document_id"] != row["source_document_id"]
                or a["physical_page"] != unit["physical_page"]
                for a in unit["evidence"]
            ):
                raise ValueError("field unit evidence belongs to another source")
            features = unit_features(unit, geometries[unit["physical_page"]])
            words = source_words(unit)
            word_labels = None
            if row["dataset_page_id"] in eligible:
                spans = [s for s in reviewed["units"] if s["node_id"] == unit["node_id"]]
                word_labels = project_word_labels(words, spans)
                counts["labelled_words"] += sum(label is not None for label in word_labels)
                counts["unresolved_word_labels"] += sum(label is None for label in word_labels)
            counts.update([features["geometry_status"], features["typography_status"]])
            counts["words"] += len(words)
            units.append(
                {
                    "node_id": unit["node_id"],
                    "physical_page": unit["physical_page"],
                    "raw_text": unit["raw_text"],
                    "text_span": unit["text_span"],
                    "evidence": unit["evidence"],
                    "words": words,
                    "features": features,
                    "word_labels": word_labels,
                }
            )
        windows.append(
            {
                "dataset_page_id": row["dataset_page_id"],
                "source_document_id": row["source_document_id"],
                "source_pdf_checksum": row["source_pdf_checksum"],
                "learning_group": row["learning_group"],
                "learning_partition": "TUNING",
                "source_order_sha256": source_order_digest(row),
                "units": units,
            }
        )
    write_jsonl(output / "common_source_inputs.jsonl", windows)
    write_jsonl(output / "page_frames.jsonl", frames)
    workspace = Path(__file__).resolve().parents[2]
    assembler_hashes = {
        p.name: sha256(p)
        for p in (workspace / "src/course_parser").glob("*.py")
        if p.name not in {"field_experiment.py", "cli.py", "__init__.py"}
    }
    contract = {
        "schema_version": VERSION,
        "status": "PREPARATION_ONLY_MODELS_DEFERRED",
        "arms": list(ARMS),
        "focus_roles": list(FOCUS_ROLES),
        "label_inventory": sorted(LABELS - {"SOURCE_INSUFFICIENT"}),
        "common_inputs_sha256": sha256(output / "common_source_inputs.jsonl"),
        "page_frames_sha256": sha256(output / "page_frames.jsonl"),
        "source_annotations_sha256": sha256(selected),
        "source_order_policy": "NATIVE_AS_EXPORTED_HUMAN_CONFIRMATION_REQUIRED",
        "shared_assembly_code_sha256": assembler_hashes,
        "model_features": [
            "raw_text",
            "bbox_1000",
            "font_size_relative",
            "bold_fraction",
            "italic_fraction",
            "availability_indicators",
        ],
        "provenance_not_features": [
            "node_id",
            "dataset_page_id",
            "learning_group",
            "source_document_id",
            "source_pdf_checksum",
            "paths",
        ],
        "apply_ocr": False,
        "box_granularity": "SOURCE_SEGMENT_REPEATED_PER_WORD",
        "token_alignment": "Use word_ids and overflow_to_sample_mapping; retain offsets. "
        "Ignore specials/repeated subwords in loss; account for every source word. "
        "A 512-subword limit is not a 512-word limit.",
        "image_ablation": "Compare identically initialized/trained LayoutLMv3 image/no-image arms, "
        "with common labels, folds, token windows, budgets and seeds. "
        "Simple classifier comparison alone does not isolate image benefit.",
        "chunking": "One page image per model sequence; retain all overflow windows and "
        "cross-page course ownership in unchanged assembly. Never silently truncate.",
        "scope": "Field-role suggestions only; no OCR replacement, source reordering, "
        "course creation, owner assignment or acceptance.",
        "adoption": "Require permitted institution-grouped validation and paired course gains, "
        "with no worse false ownership/contamination/abstentions. Token F1 auxiliary.",
    }
    write_json(output / "experiment_contract.json", contract)
    write_json(output / "annotation_readiness.json", readiness)
    result = {
        "schema_version": VERSION,
        "status": contract["status"],
        "windows": len(windows),
        "page_frames": len(frames),
        "source_units": sum(len(w["units"]) for w in windows),
        "feature_counts": dict(counts),
        "adjudicated_windows": readiness["adjudicated_windows"],
        "model_training_or_inference_run": False,
        "experiment_ready": False,
        "blockers": [
            *(
                ["NO_VALIDATED_COMMON_WORD_LABEL_DATASET"]
                if not counts["words"] or counts["labelled_words"] < counts["words"]
                else []
            ),
            "NO_PERMITTED_INSTITUTION_GROUPED_FIELD_VALIDATION_SPLIT",
            "NO_SOURCE_REVIEWED_IMAGE_DEPENDENT_FAILURE_RATIONALE",
        ],
        "protected_evaluation_opened": False,
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "manifest.json", result)
    return {k: v for k, v in result.items() if k != "file_sha256"}


# Validate shadow outputs and fair-comparison source and version history, never score or adopt
# them.
def audit_field_proposals(experiment: Path, proposals: Path) -> dict:
    """Validate shadow outputs and fair-comparison provenance, never score or adopt them."""
    _reject_reserved_paths(experiment, proposals)
    for name in (
        "manifest.json",
        "experiment_contract.json",
        "common_source_inputs.jsonl",
        "page_frames.jsonl",
    ):
        _packet_file(experiment, str(experiment / name))
    manifest = read_json(experiment / "manifest.json")
    verify_files(experiment, manifest["file_sha256"])
    contract = read_json(experiment / "experiment_contract.json")
    # Stop if the experiment contract differs from frozen source inputs or frames.
    if (
        sha256(experiment / "common_source_inputs.jsonl") != contract["common_inputs_sha256"]
        or sha256(experiment / "page_frames.jsonl") != contract["page_frames_sha256"]
    ):
        raise ValueError("experiment contract differs from frozen source inputs or frames")
    expected = {}
    for window in read_jsonl(experiment / "common_source_inputs.jsonl"):
        for unit in window["units"]:
            for word in unit["words"]:
                key = (
                    window["dataset_page_id"],
                    unit["node_id"],
                    word["text_span"]["start"],
                    word["text_span"]["end"],
                )
                expected[key] = word["raw_text"]
    runs = read_json(proposals)
    # Paired experiment requires all three arms.
    if set(runs) != set(ARMS):
        raise ValueError("paired experiment requires all three arms")
    common_keys = (
        "annotation_sha256",
        "fold_manifest_sha256",
        "training_budget_sha256",
        "seed_schedule_sha256",
        "token_windows_sha256",
    )
    signature = None
    for arm in ARMS:
        run = runs[arm]
        # Both experimental methods must use the same audited annotations.
        if run.get("annotation_sha256") != contract["source_annotations_sha256"]:
            raise ValueError("field arm uses annotations other than the audited source copy")
        # Both experimental methods must use the same source inputs.
        if run["common_inputs_sha256"] != contract["common_inputs_sha256"]:
            raise ValueError("field arm uses different source inputs")
        # Both experimental methods must use the same code to assemble courses from fields.
        if run["shared_assembly_code_sha256"] != contract["shared_assembly_code_sha256"]:
            raise ValueError("field arm changed downstream assembly")
        current = tuple(run.get(k) for k in common_keys)
        # Require the recorded labels, data splits, training budget, seed and text windows.
        if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in current):
            raise ValueError("field experiment lacks pinned labels/folds/budget/seed/token windows")
        # Keep labels, folds, training budget, random seed and text windows equivalent so the
        # method comparison is fair.
        if signature is not None and current != signature:
            raise ValueError("field arms differ in labels/folds/budget/seed/token windows")
        signature = current
        # Field model must use frozen native source, not fresh OCR.
        if run.get("apply_ocr") is not False:
            raise ValueError("field model must use frozen native source, not fresh OCR")
        observed = {}
        for item in run["predictions"]:
            key = (
                item["dataset_page_id"],
                item["node_id"],
                item["text_span"]["start"],
                item["text_span"]["end"],
            )
            # Each prediction must refer to an unchanged source span, with no duplicate or
            # invented spans.
            if key in observed or key not in expected or item["raw_text"] != expected[key]:
                raise ValueError("field prediction duplicates, invents or changes a source span")
            # Reject unsupported course field label.
            if item["label"] is not None and item["label"] not in contract["label_inventory"]:
                raise ValueError("unsupported course field label")
            observed[key] = item["label"]
        # Keep every source word represented, even when its predicted role is unresolved.
        if set(observed) != set(expected):
            raise ValueError("field predictions omitted source words; use explicit abstentions")
    # The experiment must clearly identify which method uses page images.
    if (
        runs[ARMS[1]].get("visual_embed") is not False
        or runs[ARMS[2]].get("visual_embed") is not True
    ):
        raise ValueError("image and no-image ablations are not distinguished")
    # Start both image and text-only methods from the same checkpoint for a fair comparison.
    if runs[ARMS[1]].get("initial_checkpoint_sha256") != runs[ARMS[2]].get(
        "initial_checkpoint_sha256"
    ):
        raise ValueError("image/no-image arms start from different checkpoints")
    checkpoint = runs[ARMS[1]].get("initial_checkpoint_sha256")
    # Image ablation requires a pinned initial checkpoint.
    if not isinstance(checkpoint, str) or not re.fullmatch(r"[0-9a-f]{64}", checkpoint):
        raise ValueError("image ablation requires a pinned initial checkpoint")
    return {
        "status": "SHADOW_PROVENANCE_AND_COVERAGE_VALIDATED_NOT_SCORED",
        "source_words_per_arm": len(expected),
        "model_accuracy_measured": False,
        "training_provenance_independently_verified": False,
        "automatic_adoption": False,
        "protected_evaluation_opened": False,
    }
