# Link existing keyword hits to the PDF pages, text and images they came from. This creates
# the evidence reviewers need without deciding which course owns a hit.
"""Connect frozen hits to bounded v3 page evidence without running course inference."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import fitz

from . import __version__
from .io import (
    artifact_hashes,
    contained_file,
    map_source_path,
    read_csv,
    read_json,
    read_jsonl,
    sha256,
    verify_files,
    write_csv,
    write_json,
    write_jsonl,
)
from .reference import ReferenceProject
from .release import TaskRelease
from .report import write_report

READY = "SOURCE_EVIDENCE_READY"
INCOMPLETE = "SOURCE_EVIDENCE_INCOMPLETE"


# Build a stable key identifying one physical page of one source document.
def page_key(task: dict, number: int) -> str:
    return f"{task['search_source_id']}:{task['source_pdf_checksum']}:p{number}"


# Select the bounded physical pages needed around a central page.
def context_pages(task: dict) -> list[int]:
    return sorted(
        int(task[key])
        for key in ("previous_physical_page", "central_physical_page", "next_physical_page")
        if task[key]
    )


# Check the page packet against its recorded source identity and artifact checksums.
def verify_packet(packet: Path, release: TaskRelease) -> tuple[list[dict], dict]:
    qc = read_json(packet / "materialization_qc.json")
    # Stop if the packet belongs to a different task release.
    if qc.get("task_release_manifest_sha256") != release.audit()["task_release_manifest_sha256"]:
        raise ValueError("packet belongs to a different task release")
    for name, key in (
        ("batch_task_status.csv", "batch_status_sha256"),
        ("batch_hit_disposition_template.csv", "batch_hit_template_sha256"),
    ):
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(packet / name) != qc.get(key):
            raise ValueError(f"packet artifact checksum changed: {name}")
    statuses = read_csv(packet / "batch_task_status.csv")
    inventory = {task["page_task_id"]: task for task in release.tasks}
    # A packet must contain unique page-task results rather than an empty or repeated status
    # list.
    if not statuses or len({row["page_task_id"] for row in statuses}) != len(statuses):
        raise ValueError("empty packet or repeated page-task status")
    for row in statuses:
        task = inventory.get(row["page_task_id"])
        # Stop if the packet status differs from frozen task identity.
        if task is None or any(
            row[key] != task[key]
            for key in ("review_rank", "search_source_id", "central_physical_page", "hit_count")
        ):
            raise ValueError("packet status differs from frozen task identity")
        # Reject unknown packet materialization status.
        if row["materialization_status"] not in {"READY_FOR_HUMAN_SOURCE_REVIEW", INCOMPLETE}:
            raise ValueError("unknown packet materialization status")
        # Stop if the packet already contains review state.
        if row["review_status"] != "PENDING_SOURCE_REVIEW":
            raise ValueError("packet already contains review state")
        if row["materialization_status"] == "READY_FOR_HUMAN_SOURCE_REVIEW":
            # Stop if the ready packet is missing its source-review HTML.
            if not row["html_path"] or not contained_file(packet, row["html_path"]).is_file():
                raise ValueError("ready packet is missing its source-review HTML")
    expected = {
        hit["hit_id"]: row["page_task_id"]
        for row in statuses
        for hit in release.hits[row["page_task_id"]]
    }
    template = read_csv(packet / "batch_hit_disposition_template.csv")
    # Stop if the packet blank template differs from selected original hits.
    if (
        len(template) != len(expected)
        or {row["hit_id"]: row["page_task_id"] for row in template} != expected
        or any(row.get("decision") for row in template)
    ):
        raise ValueError("packet blank template differs from selected original hits")
    ready_count = sum(
        row["materialization_status"] == "READY_FOR_HUMAN_SOURCE_REVIEW" for row in statuses
    )
    # The packet's quality counts and review policy must agree with its saved evidence.
    if (
        qc.get("status")
        != (
            "SOURCE_REVIEW_TASKS_READY"
            if ready_count == len(statuses)
            else "SOURCE_MATERIAL_GAPS_BLOCK_REVIEW"
        )
        or qc.get("selected_tasks") != len(statuses)
        or qc.get("selected_original_hits") != len(expected)
        or qc.get("ready_tasks") != ready_count
        or qc.get("incomplete_tasks") != len(statuses) - ready_count
        or qc.get("course_truth_assigned") is not False
        or qc.get("review_dispositions_assigned") != 0
    ):
        raise ValueError("packet QC counts or truth policy differ from its artifacts")
    return sorted(statuses, key=lambda row: int(row["review_rank"])), qc


# Index the original frozen page text without accepting replacement text.
def frozen_pages(path: Path, task: dict, needed: set[int]) -> dict[int, dict]:
    found = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            page = json.loads(line)
            number = int(page["page_number"])
            if number not in needed:
                continue
            # Stop if the frozen page identity or text differs from its source task.
            if (
                number in found
                or page["search_source_id"] != task["search_source_id"]
                or page["source_pdf_checksum"] != task["source_pdf_checksum"]
                or page["page_index"] != number - 1
                or not isinstance(page.get("raw_text"), (str, type(None)))
            ):
                raise ValueError("frozen page identity or text differs from its source task")
            found[number] = page
    # Every requested neighboring page must be present in the saved text stream.
    if set(found) != needed:
        raise ValueError(f"frozen stream is missing context pages: {sorted(needed - set(found))}")
    return found


# Retain an explicit page result when its source evidence could not be recovered.
def empty_page(task: dict, number: int) -> dict:
    return {
        "page_key": page_key(task, number),
        "source_id": task["search_source_id"],
        "source_pdf_checksum": task["source_pdf_checksum"],
        "physical_page": number,
        "evidence_status": INCOMPLETE,
        "image_relative_path": "",
        "image_sha256": None,
        "image_binding_status": "UNVERIFIED",
        "v3_page_source": None,
        "errors": [],
    }


# Create bounded page evidence for the existing keyword-hit tasks and retain pending
# decisions.
def prepare_bridge(
    reference: ReferenceProject,
    release: TaskRelease,
    packet: Path,
    output: Path,
    *,
    remote_root: Path | None = None,
    local_root: Path | None = None,
) -> dict:
    packet = packet.resolve()
    output = output.resolve()
    # Both source mapping roots are required.
    if (remote_root is None) != (local_root is None):
        raise ValueError("both source mapping roots are required")
    statuses, _ = verify_packet(packet, release)
    task_inventory = {task["page_task_id"]: task for task in release.tasks}
    tasks = [task_inventory[row["page_task_id"]] for row in statuses]
    output.mkdir(parents=True, exist_ok=False)
    try:
        return _prepare(
            reference, release, packet, output, tasks, statuses, remote_root, local_root
        )
    except Exception as exc:
        write_json(
            output / "run_failure.json",
            {"status": "FAILED", "error": str(exc), "ownership_truth_assigned": False},
        )
        raise


# Collect the verified page and hit evidence needed for the bounded output batch.
def _prepare(reference, release, packet, output, tasks, statuses, remote_root, local_root):
    (output / "assets").mkdir()
    input_hashes = {
        str(packet / name): sha256(packet / name)
        for name in (
            "materialization_qc.json",
            "batch_task_status.csv",
            "batch_hit_disposition_template.csv",
        )
    }
    input_hashes[str(release.vocabulary_path)] = sha256(release.vocabulary_path)
    input_hashes[str(release.root / "task_release_manifest.json")] = sha256(
        release.root / "task_release_manifest.json"
    )
    for name, digest in release.manifest["files_sha256"].items():
        input_hashes[str(contained_file(release.root, name))] = digest
    pages = {
        page_key(task, number): empty_page(task, number)
        for task in tasks
        for number in context_pages(task)
    }
    typed_pages = {}
    groups = defaultdict(list)
    ready_ids = {
        row["page_task_id"]
        for row in statuses
        if row["materialization_status"] == "READY_FOR_HUMAN_SOURCE_REVIEW"
    }
    for task in tasks:
        if task["page_task_id"] in ready_ids:
            groups[task["search_source_id"]].append(task)
    for source_id, group in groups.items():
        base = group[0]
        needed = {number for task in group for number in context_pages(task)}
        try:
            pdf_path = map_source_path(base["source_pdf_path"], remote_root, local_root)
            stream_path = map_source_path(base["page_stream_path"], remote_root, local_root)
            for path, expected in (
                (pdf_path, base["source_pdf_checksum"]),
                (stream_path, base["page_stream_sha256"]),
            ):
                # Reject this input if its file fingerprint no longer matches the recorded
                # version.
                if sha256(path) != expected:
                    raise ValueError(f"source artifact checksum differs: {path}")
                input_hashes[str(path.resolve())] = expected
            records = frozen_pages(stream_path, base, needed)
            with fitz.open(pdf_path) as pdf:
                # All requested neighboring pages must exist in the verified PDF.
                if max(needed) > len(pdf):
                    raise ValueError("context page exceeds verified PDF page count")
                for number in sorted(needed):
                    observed = pages[page_key(base, number)]
                    try:
                        original = contained_file(packet, f"assets/{source_id}_p{number:05d}.png")
                        # Stop if the packet page image is missing.
                        if not original.is_file():
                            raise ValueError("packet page image is missing")
                        actual = fitz.Pixmap(str(original))
                        pdf_page = pdf[number - 1]
                        expected = pdf_page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                        # The saved page image must match the verified render of this PDF
                        # page.
                        if (actual.width, actual.height, actual.n, actual.alpha) != (
                            expected.width,
                            expected.height,
                            expected.n,
                            expected.alpha,
                        ) or actual.samples != expected.samples:
                            raise ValueError(
                                "packet image pixels differ from the verified PDF render"
                            )
                        digest = sha256(original)
                        input_hashes[str(original)] = digest
                        name = hashlib.sha256(observed["page_key"].encode()).hexdigest() + ".png"
                        image_path = output / "assets" / name
                        shutil.copyfile(original, image_path)
                        page_source = reference.page_source(
                            source_id=source_id,
                            checksum=base["source_pdf_checksum"],
                            page=records[number],
                            image_path=image_path,
                            image_hash=digest,
                            width=pdf_page.rect.width,
                            height=pdf_page.rect.height,
                            rotation=pdf_page.rotation,
                            native_text=pdf_page.get_text("text"),
                        )
                        typed_pages[observed["page_key"]] = page_source
                        observed.update(
                            evidence_status=READY,
                            image_relative_path=f"assets/{name}",
                            image_sha256=digest,
                            image_binding_status="MATCHES_VERIFIED_PDF_RENDER",
                            v3_page_source=page_source.model_dump(mode="json"),
                        )
                    # Handle oserror, value error, runtime error, fitz's mupdf's fz error base
                    # by keeping the failure explicit or using the documented fallback.
                    except (OSError, ValueError, RuntimeError, fitz.mupdf.FzErrorBase) as exc:
                        observed["errors"].append(str(exc))
        # Handle oserror, value error, runtime error, eoferror and the other listed values by
        # keeping the failure explicit or using the documented fallback.
        except (OSError, ValueError, RuntimeError, EOFError, fitz.mupdf.FzErrorBase) as exc:
            for number in needed:
                pages[page_key(base, number)]["errors"].append(str(exc))
    task_records, hit_records = [], []
    for task, packet_status in zip(tasks, statuses, strict=True):
        keys = [page_key(task, number) for number in context_pages(task)]
        errors = [error for key in keys for error in pages[key]["errors"]]
        if task["page_task_id"] not in ready_ids:
            reason = packet_status["error"] or "packet source evidence is incomplete"
            errors.append(reason)
            for key in keys:
                if not pages[key]["errors"] and pages[key]["evidence_status"] != READY:
                    pages[key]["errors"].append(reason)
        task_ready = task["page_task_id"] in ready_ids and all(
            pages[key]["evidence_status"] == READY for key in keys
        )
        task_hits = []
        for original_hit in release.hits[task["page_task_id"]]:
            anchors, fragments, hit_errors = [], [], []
            for span in json.loads(original_hit["source_spans_json"]):
                key = page_key(task, span["page_index"] + 1)
                page_source = typed_pages.get(key)
                if page_source is None:
                    hit_errors.append(f"source page unavailable: {span['page_index'] + 1}")
                    continue
                try:
                    anchor = reference.anchor(
                        task["search_source_id"],
                        task["source_pdf_checksum"],
                        page_source,
                        span["raw_start"],
                        span["raw_end"],
                    )
                    anchors.append(anchor.model_dump(mode="json"))
                    fragments.append(anchor.observed_text)
                except ValueError as exc:
                    hit_errors.append(str(exc))
            quote = "\n".join(fragments) if not hit_errors else None
            if quote is not None:
                normalized = reference.normalize(quote)
                matches = reference.matcher.match_text(quote, release.vocabulary)
                if not any(
                    match["canonical_phrase"] == original_hit["canonical_concept"]
                    and match["normalized_start"] == 0
                    and match["normalized_end"] == len(normalized)
                    for match in matches
                ):
                    hit_errors.append(
                        "original source quote does not support the claimed lexical hit"
                    )
            hit_ready = task_ready and not hit_errors
            record = {
                **original_hit,
                "source_pdf_checksum": task["source_pdf_checksum"],
                "original_source_spans": json.loads(original_hit["source_spans_json"]),
                "source_quote": quote,
                "v3_evidence_anchors": anchors,
                "evidence_status": READY if hit_ready else INCOMPLETE,
                "errors": hit_errors,
                "review_status": "PENDING_SOURCE_REVIEW",
                "decision": "",
                "proposed_owner": None,
            }
            task_hits.append(record)
        if any(hit["evidence_status"] != READY for hit in task_hits):
            task_ready = False
            errors.extend(error for hit in task_hits for error in hit["errors"])
            for hit in task_hits:
                hit["evidence_status"] = INCOMPLETE
        hit_records.extend(task_hits)
        task_records.append(
            {
                **task,
                "central_physical_page": int(task["central_physical_page"]),
                "hit_count": int(task["hit_count"]),
                "page_keys": keys,
                "evidence_status": READY if task_ready else INCOMPLETE,
                "review_status": "PENDING_SOURCE_REVIEW",
                "errors": sorted(set(errors)),
            }
        )
    coverage = {
        "scope": "BOUNDED_FROZEN_HIT_REVIEW_CONTEXT",
        "complete_source_documents": False,
        "catalog_negative_inference_allowed": False,
        "ownership_truth_assigned": False,
        "selected_tasks": len(task_records),
        "selected_original_hits": len(hit_records),
        "task_status_counts": dict(Counter(row["evidence_status"] for row in task_records)),
        "hit_status_counts": dict(Counter(row["evidence_status"] for row in hit_records)),
        "unique_context_pages": len(pages),
        "page_status_counts": dict(Counter(row["evidence_status"] for row in pages.values())),
        "pending_dispositions": len(hit_records),
        "finalized_dispositions": 0,
    }
    # Retain each original keyword hit exactly once in the evidence bridge.
    if len({row["hit_id"] for row in hit_records}) != len(hit_records):
        raise ValueError("bridge duplicated an original hit")
    write_jsonl(output / "page_evidence.jsonl", list(pages.values()))
    write_jsonl(output / "task_evidence.jsonl", task_records)
    write_jsonl(output / "hit_evidence.jsonl", hit_records)
    write_json(output / "coverage.json", coverage)
    write_json(output / "release_audit.json", release.audit())
    template = [
        {
            "page_task_id": row["page_task_id"],
            "hit_id": row["hit_id"],
            "decision": "",
            "source_course_id": "",
            "keyword_field": "",
            "source_reason": "",
            "unresolved_reason": "",
        }
        for row in hit_records
    ]
    write_csv(output / "hit_disposition_template.csv", template, list(template[0]))
    write_report(output, task_records, list(pages.values()), hit_records)
    code = {
        str(path.relative_to(Path(__file__).parent)): sha256(path)
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    manifest = {
        "schema_version": "course-parser-evidence-bridge-v1",
        "software_version": __version__,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "READY_FOR_SOURCE_REVIEW"
        if all(row["evidence_status"] == READY for row in task_records)
        else INCOMPLETE,
        "source_scope": coverage["scope"],
        "ownership_truth_assigned": False,
        "catalog_negative_inference_allowed": False,
        "render_binding": {
            "scale": 2.5,
            "pymupdf_version": fitz.VersionBind,
            "comparison": "EXACT_DECODED_PIXELS",
        },
        "input_sha256": input_hashes,
        "reference_code_sha256": reference.provenance(),
        "software_code_sha256": code,
        "files_sha256": artifact_hashes(output),
    }
    write_json(output / "run_manifest.json", manifest)
    return {"status": manifest["status"], "output": str(output), **coverage}


# Verify the stored run inputs, file checksums and hit-accounting relationships.
def verify_run(output: Path, *, recheck_inputs: bool = False) -> dict:
    manifest = read_json(output / "run_manifest.json")
    # Reject unsupported run or incompatible evidence scope.
    if (
        manifest.get("schema_version") != "course-parser-evidence-bridge-v1"
        or manifest.get("source_scope") != "BOUNDED_FROZEN_HIT_REVIEW_CONTEXT"
        or manifest.get("ownership_truth_assigned") is not False
        or manifest.get("catalog_negative_inference_allowed") is not False
    ):
        raise ValueError("unsupported run or incompatible evidence scope")
    required = {
        "page_evidence.jsonl",
        "task_evidence.jsonl",
        "hit_evidence.jsonl",
        "coverage.json",
        "release_audit.json",
        "hit_disposition_template.csv",
        "index.html",
    }
    # Record every required evidence file in the run manifest.
    if not required.issubset(manifest["files_sha256"]):
        raise ValueError("run manifest omits required evidence artifacts")
    verify_files(output, manifest["files_sha256"])
    hits = read_jsonl(output / "hit_evidence.jsonl")
    tasks = read_jsonl(output / "task_evidence.jsonl")
    pages = read_jsonl(output / "page_evidence.jsonl")
    coverage = read_json(output / "coverage.json")
    # Stop if the run hit/task accounting or pending-decision policy differs.
    if (
        len(hits) != len({hit["hit_id"] for hit in hits})
        or any(hit["decision"] or hit["proposed_owner"] is not None for hit in hits)
        or len(tasks) != len({task["page_task_id"] for task in tasks})
        or len(pages) != len({page["page_key"] for page in pages})
        or coverage["selected_original_hits"] != len(hits)
        or coverage["selected_tasks"] != len(tasks)
        or coverage["unique_context_pages"] != len(pages)
        or coverage["pending_dispositions"] != len(hits)
        or coverage["finalized_dispositions"] != 0
    ):
        raise ValueError("run hit/task accounting or pending-decision policy differs")
    _verify_records(output, manifest, coverage, tasks, pages, hits)
    if recheck_inputs:
        for path, expected in {
            **manifest["input_sha256"],
            **manifest["reference_code_sha256"],
        }.items():
            # Stop if the preparation input or reference code changed.
            if sha256(Path(path)) != expected:
                raise ValueError(f"preparation input or reference code changed: {path}")
    return {
        "status": "PASS",
        "batch_status": manifest["status"],
        "selected_tasks": len(tasks),
        "original_hits": len(hits),
        "inputs_rechecked": recheck_inputs,
        "ownership_truth_assigned": False,
        "catalog_negative_inference_allowed": False,
    }


# Check that the output records preserve each original task, hit and source relationship.
def _verify_records(output, manifest, coverage, tasks, pages, hits):
    page_map = {page["page_key"]: page for page in pages}
    task_map = {task["page_task_id"]: task for task in tasks}
    by_task = defaultdict(list)
    expected_coverage = {
        "scope": "BOUNDED_FROZEN_HIT_REVIEW_CONTEXT",
        "complete_source_documents": False,
        "catalog_negative_inference_allowed": False,
        "ownership_truth_assigned": False,
        "task_status_counts": dict(Counter(row["evidence_status"] for row in tasks)),
        "hit_status_counts": dict(Counter(row["evidence_status"] for row in hits)),
        "page_status_counts": dict(Counter(row["evidence_status"] for row in pages)),
    }
    # The coverage totals and declared scope must agree with the saved page evidence.
    if any(coverage.get(key) != value for key, value in expected_coverage.items()):
        raise ValueError("coverage status counts or evidence scope differ")
    expected_status = (
        "READY_FOR_SOURCE_REVIEW"
        if all(task["evidence_status"] == READY for task in tasks)
        else INCOMPLETE
    )
    # Stop if the run status differs from task evidence.
    if not tasks or manifest["status"] != expected_status:
        raise ValueError("run status differs from task evidence")
    for record in [*pages, *tasks, *hits]:
        # Reject unknown evidence status.
        if record["evidence_status"] not in {READY, INCOMPLETE}:
            raise ValueError("unknown evidence status")
        # Stop if the ready evidence contains unresolved errors.
        if record["evidence_status"] == READY and record["errors"]:
            raise ValueError("ready evidence contains unresolved errors")
    for page in pages:
        if page["evidence_status"] != READY:
            continue
        source = page["v3_page_source"]
        image = contained_file(output, page["image_relative_path"])
        # Stop if the page evidence image or representation binding differs.
        if (
            source is None
            or source["physical_page"] != page["physical_page"]
            or source["image_sha256"] != page["image_sha256"]
            or sha256(image) != page["image_sha256"]
            or page["image_relative_path"] not in manifest["files_sha256"]
            or page["image_binding_status"] != "MATCHES_VERIFIED_PDF_RENDER"
            or len(source["representations"]) != 1
        ):
            raise ValueError("page evidence image or representation binding differs")
        representation = source["representations"][0]
        # Stop if the page representation has incompatible source text.
        if (
            not isinstance(representation["raw_text"], str)
            or representation["physical_page"] != page["physical_page"]
        ):
            raise ValueError("page representation has incompatible source text")
    for hit in hits:
        task = task_map.get(hit["page_task_id"])
        # Stop if the original hit differs from task source identity.
        if (
            task is None
            or any(
                hit[key] != task[key]
                for key in ("search_source_id", "source_pdf_checksum", "document_id")
            )
            or int(hit["physical_page"]) != task["central_physical_page"]
        ):
            raise ValueError("original hit differs from task source identity")
        # Keep each hit's review state and original source positions unchanged.
        if (
            hit["review_status"] != "PENDING_SOURCE_REVIEW"
            or hit["evidence_status"] != task["evidence_status"]
            or hit["original_source_spans"] != json.loads(hit["source_spans_json"])
        ):
            raise ValueError("hit review state or frozen source spans differ")
        by_task[hit["page_task_id"]].append(hit["hit_id"])
        anchors = hit["v3_evidence_anchors"]
        fragments = []
        for anchor in anchors:
            key = page_key(task, anchor["physical_page"])
            page = page_map.get(key)
            # Each hit location needs the page evidence it points to.
            if page is None or page["v3_page_source"] is None or key not in task["page_keys"]:
                raise ValueError("hit anchor refers to unavailable page evidence")
            representation = page["v3_page_source"]["representations"][0]
            span = anchor["text_span"]
            original_span = {
                "page_index": anchor["physical_page"] - 1,
                "raw_start": span["start"],
                "raw_end": span["end"],
            }
            # Stop if the hit anchor differs from its exact frozen source span.
            if (
                original_span not in hit["original_source_spans"]
                or anchor["source_document_id"] != task["search_source_id"]
                or anchor["source_pdf_checksum"] != task["source_pdf_checksum"]
                or anchor["representation_id"] != representation["representation_id"]
                or anchor["extraction_method"] != representation["extraction_method"]
                or not 0 <= span["start"] < span["end"] <= len(representation["raw_text"])
                or anchor["observed_text"]
                != representation["raw_text"][span["start"] : span["end"]]
            ):
                raise ValueError("hit anchor differs from its exact frozen source span")
            fragments.append(anchor["observed_text"])
        # Stop if the hit quote differs from its evidence anchors.
        if hit["source_quote"] is not None and hit["source_quote"] != "\n".join(fragments):
            raise ValueError("hit quote differs from its evidence anchors")
        # Stop if the ready hit lacks complete source anchors.
        if hit["evidence_status"] == READY and (
            hit["source_quote"] is None or len(anchors) != len(hit["original_source_spans"])
        ):
            raise ValueError("ready hit lacks complete source anchors")
    for task in tasks:
        keys = [page_key(task, number) for number in context_pages(task)]
        # Stop if the task context or original-hit accounting differs.
        if (
            task["page_keys"] != keys
            or any(key not in page_map for key in keys)
            or set(by_task[task["page_task_id"]]) != set(json.loads(task["hit_ids_json"]))
            or len(by_task[task["page_task_id"]]) != task["hit_count"]
            or task["review_status"] != "PENDING_SOURCE_REVIEW"
            or (
                task["evidence_status"] == READY
                and any(page_map[key]["evidence_status"] != READY for key in keys)
            )
        ):
            raise ValueError("task context or original-hit accounting differs")
    template = read_csv(output / "hit_disposition_template.csv")
    # Stop if the blank disposition template differs from pending hit inventory.
    if (
        len(template) != len(hits)
        or {row["hit_id"]: row["page_task_id"] for row in template}
        != {hit["hit_id"]: hit["page_task_id"] for hit in hits}
        or any(
            value
            for row in template
            for key, value in row.items()
            if key not in {"hit_id", "page_task_id"}
        )
    ):
        raise ValueError("blank disposition template differs from pending hit inventory")
