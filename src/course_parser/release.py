# Validate a saved batch of keyword-review tasks before selecting a smaller working batch.
# Preserve all original hits and their links to source pages.
"""Validate the complete frozen release before selecting a bounded batch."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .io import read_csv, read_json, sha256, verify_files
from .reference import ReferenceProject


# Hold a verified task batch and the original keyword hits linked to each task.
@dataclass
class TaskRelease:
    root: Path
    manifest: dict
    tasks: list[dict]
    hits: dict[str, list[dict]]
    vocabulary: dict
    vocabulary_path: Path

    # Read and validate the complete release before allowing a bounded selection.
    @classmethod
    def load(cls, root: Path, vocabulary_path: Path, reference: ReferenceProject):
        root = root.resolve()
        manifest = read_json(root / "task_release_manifest.json")
        # Reject unsupported task release.
        if manifest.get("release_id") != "supervisor-keyword-v2-page-review-tasks-v1":
            raise ValueError("unsupported task release")
        policies = {
            "hits_accounted_exactly_once": True,
            "ownership_truth_assigned": False,
            "ranking_is_truth": False,
            "all_low_ranked_hits_retained": True,
        }
        # Stop if the task release has incompatible accounting or truth policy.
        if any(manifest.get(key) is not value for key, value in policies.items()):
            raise ValueError("task release has incompatible accounting or truth policy")
        hashes = manifest.get("files_sha256", {})
        required = {
            "page_review_tasks.csv",
            "task_hit_crosswalk.csv",
            "hit_disposition_template.csv",
        }
        # Require recorded file fingerprints before trusting this saved input.
        if not required.issubset(hashes):
            raise ValueError("task release lacks required artifact hashes")
        verify_files(root, hashes)
        vocabulary = reference.vocabulary(vocabulary_path)
        concepts = {item["canonical_phrase"] for item in vocabulary["concepts"]}
        tasks = read_csv(root / "page_review_tasks.csv")
        by_id = {row["page_task_id"]: row for row in tasks}
        # Reject duplicate tasks or declared page count mismatch.
        if len(by_id) != len(tasks) or len(tasks) != manifest["page_tasks"]:
            raise ValueError("duplicate tasks or declared page count mismatch")
        ranks = [int(row["review_rank"]) for row in tasks]
        # Stop if the review ranks are not a unique complete sequence.
        if sorted(ranks) != list(range(1, len(tasks) + 1)):
            raise ValueError("review ranks are not a unique complete sequence")
        identities = {}
        for task in tasks:
            # The inherited renderer uses these identifiers in artifact filenames.
            for key in ("page_task_id", "search_source_id"):
                # Reject unsafe artifact identifier.
                if not re.fullmatch(r"[A-Za-z0-9_-]+", task[key]):
                    raise ValueError(f"unsafe artifact identifier: {key}")
            number = int(task["central_physical_page"])
            # Reject invalid physical page or source identity.
            if number < 1 or not task["search_source_id"]:
                raise ValueError("invalid physical page or source identity")
            identity = tuple(
                task[key]
                for key in (
                    "source_pdf_checksum",
                    "source_pdf_path",
                    "page_stream_path",
                    "page_stream_sha256",
                )
            )
            old = identities.setdefault(task["search_source_id"], identity)
            # Stop if the tasks for one source have conflicting source identities.
            if old != identity:
                raise ValueError("tasks for one source have conflicting source identities")
            for key in ("source_pdf_checksum", "page_stream_sha256"):
                value = task[key]
                # Reject invalid SHA256 in lookup key.
                if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise ValueError(f"invalid SHA256 in {key}")
            for key, expected in (
                ("previous_physical_page", number - 1),
                ("next_physical_page", number + 1),
            ):
                # Stop if the context pages are not adjacent to the central page.
                if task[key] and (int(task[key]) != expected or int(task[key]) < 1):
                    raise ValueError("context pages are not adjacent to the central page")
        hits = defaultdict(list)
        seen = set()
        approved_forms = {}
        for hit in read_csv(root / "task_hit_crosswalk.csv"):
            task = by_id.get(hit["page_task_id"])
            # Every original hit must appear exactly once and belong to a known task.
            if task is None or not hit["hit_id"] or hit["hit_id"] in seen:
                raise ValueError("orphan or repeated original hit ID")
            seen.add(hit["hit_id"])
            # Stop if the hit identity differs from its page task.
            if (
                hit["search_source_id"] != task["search_source_id"]
                or hit["document_id"] != task["document_id"]
                or hit["physical_page"] != task["central_physical_page"]
            ):
                raise ValueError("hit identity differs from its page task")
            # A hit must refer to a concept in the approved keyword vocabulary.
            if hit["canonical_concept"] not in concepts:
                raise ValueError("hit references an unapproved concept")
            form_key = (hit["canonical_concept"], hit["matched_supervisor_form"])
            if form_key not in approved_forms:
                # The frozen crosswalk can join multiple approved spelling labels for one
                # physical occurrence. These are labels, not the source quote.
                forms = form_key[1].split(";")
                approved_forms[form_key] = all(
                    form.strip()
                    and any(
                        match["canonical_phrase"] == form_key[0]
                        and match["normalized_start"] == 0
                        and match["normalized_end"] == len(reference.normalize(form))
                        for match in reference.matcher.match_text(form, vocabulary)
                    )
                    for form in forms
                )
            # A hit must use one of the approved printed forms of its keyword.
            if not approved_forms[form_key]:
                raise ValueError("hit references an unapproved written form")
            spans = json.loads(hit["source_spans_json"])
            allowed_pages = {
                int(task[key])
                for key in ("central_physical_page", "previous_physical_page", "next_physical_page")
                if task[key]
            }
            # Stop if the original hit has no source spans.
            if not isinstance(spans, list) or not spans:
                raise ValueError("original hit has no source spans")
            for span in spans:
                values = [span.get(key) for key in ("page_index", "raw_start", "raw_end")]
                # Reject malformed or out-of-context original source span.
                if (
                    any(type(value) is not int for value in values)
                    or values[0] + 1 not in allowed_pages
                    or not 0 <= values[1] < values[2]
                ):
                    raise ValueError("malformed or out-of-context original source span")
            hits[hit["page_task_id"]].append(hit)
        # The recorded hit count must agree with the actual hit records.
        if len(seen) != manifest["original_physical_hits"]:
            raise ValueError("declared physical hit count mismatch")
        for task in tasks:
            ids = json.loads(task["hit_ids_json"])
            actual = {hit["hit_id"] for hit in hits[task["page_task_id"]]}
            # Stop if the task hit inventory differs from crosswalk.
            if (
                len(ids) != len(set(ids))
                or set(ids) != actual
                or len(actual) != int(task["hit_count"])
            ):
                raise ValueError("task hit inventory differs from crosswalk")
        template = read_csv(root / "hit_disposition_template.csv")
        # Stop if the frozen blank disposition template does not account for every hit.
        if (
            {row["hit_id"] for row in template} != seen
            or len(template) != len(seen)
            or any(row.get("decision") for row in template)
            or {row["hit_id"]: row["page_task_id"] for row in template}
            != {hit["hit_id"]: task_id for task_id, records in hits.items() for hit in records}
        ):
            raise ValueError("frozen blank disposition template does not account for every hit")
        return cls(
            root,
            manifest,
            sorted(tasks, key=lambda row: int(row["review_rank"])),
            dict(hits),
            vocabulary,
            vocabulary_path.resolve(),
        )

    # Select the requested bounded tasks from the already validated release.
    def select(self, start_rank: int, count: int) -> list[dict]:
        # Rank/count must select at least one existing task.
        if start_rank < 1 or count < 1 or start_rank > len(self.tasks):
            raise ValueError("rank/count must select at least one existing task")
        return self.tasks[start_rank - 1 : start_rank - 1 + count]

    # Report the release counts and preserved original-hit accounting.
    def audit(self) -> dict:
        return {
            "status": "PASS",
            "page_tasks": len(self.tasks),
            "physical_hits": sum(map(len, self.hits.values())),
            "concepts": 33,
            "approved_forms": 68,
            "hits_accounted_exactly_once": True,
            "ownership_truth_assigned": False,
            "scope": "FROZEN_PHYSICAL_HIT_QUEUE_ONLY",
            "catalog_negative_inference_allowed": False,
            "task_release_manifest_sha256": sha256(self.root / "task_release_manifest.json"),
            "vocabulary_sha256": sha256(self.vocabulary_path),
        }
