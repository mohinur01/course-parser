# Rebuild the text-accounting ledger from approved cached parser results. Check that every
# extracted text piece is represented without opening reserved sources or review answers.
"""Retention audit of pinned exposed source-only caches; no answer imports or scoring."""

from __future__ import annotations

import html
import importlib
from collections import Counter, defaultdict
from pathlib import Path

from .evaluation_design import (
    _metadata_file,
    audit_split_metadata,
    evaluation_role,
    load_exposed_split_metadata,
)
from .io import artifact_hashes, read_json, read_jsonl, sha256, write_json, write_jsonl
from .omission_review import _reject_reserved_paths
from .page_accounting import VERSION, build_page_accounting
from .table_structure import layout_conservation, table_contexts

LIMITS = [
    "PASS verifies retention of extracted native cells and their characters, "
    "not complete PDF text recovery.",
    "Every disposition is a parser proposal. Unresolved text remains visible; "
    "it is not a verified omission.",
    "PAGE_FURNITURE preserves the existing role without inventing a header/footer distinction.",
    "No shared-row ownership contract exists: multiple courses owning one cell are rejected. "
    "Joint identifiers in one course record remain supported.",
    "Counts include every selected context window, including overlaps; "
    "they are not independent corpus observations.",
    "The cached course records, boundaries, reading order and strict scorer are unchanged. "
    "No accuracy gain is claimed.",
    "No review answers, source images or reserved cohort were opened. "
    "The reserved cohort stays sealed "
    "until all 18 phases are complete and the parser is frozen.",
]


# Bind typed cached stages to one approved source window, then reconstruct the ledger.
def account_cached_window(reference, metadata, proposal, records):
    """Bind typed cached stages to one approved source window, then reconstruct the ledger."""
    # Accounting must use proposals for this page window, generated without review answers.
    if (
        proposal["dataset_page_id"] != metadata["dataset_page_id"]
        or proposal["source_truth_read"] is not False
    ):
        raise ValueError("accounting proposal belongs to another window or read truth")
    m = reference.models
    module = importlib.import_module("catalog_mining.course_extraction_v3.layout")
    native = tuple(module.PageLayoutProposal.model_validate(p) for p in proposal["source_layouts"])
    # Stop if the cached native layouts do not cover the bound context window.
    if (
        len(native) != len(metadata["context_physical_pages"])
        or {p.physical_page for p in native} != set(metadata["context_physical_pages"])
        or any(p.source_pdf_checksum != metadata["source_pdf_checksum"] for p in native)
        or len({p.source_document_id for p in native}) != 1
    ):
        raise ValueError("cached native layouts do not cover the bound context window")
    identity = m.SourceDocumentIdentity(
        source_document_id=native[0].source_document_id,
        source_pdf_checksum=metadata["source_pdf_checksum"],
        source_pdf_path="/cached-source-not-opened.pdf",
    )
    source_graph = m.DocumentGraph(
        identity=identity, pages=tuple(module.proposal_to_page_graph(p) for p in native)
    )
    if proposal["status"] == "PARSER_STAGE_FAILED":
        # A failed parser stage must not also claim to have emitted courses.
        if records:
            raise ValueError("failed cached parser stage emitted courses")
        ledger = build_page_accounting(source_graph, (), ())
        ledger["failure_fallback_source_only"] = True
        return ledger
    # Reject unsupported cached parser stage status.
    if proposal["status"] != "PROPOSED":
        raise ValueError("unsupported cached parser stage status")
    layouts = tuple(module.PageLayoutProposal.model_validate(p) for p in proposal["layouts"])
    by_page = {p.physical_page: p for p in layouts}
    # Stop if the cached adopted layouts changed the page inventory.
    if len(by_page) != len(native) or by_page.keys() != {p.physical_page for p in native}:
        raise ValueError("cached adopted layouts changed the page inventory")
    for p in native:
        layout_conservation(p, by_page[p.physical_page])
    graph = m.DocumentGraph(
        identity=identity, pages=tuple(module.proposal_to_page_graph(p) for p in layouts)
    )
    blocks = tuple(m.CourseBlock.model_validate(x) for x in proposal["blocks"])
    candidates = tuple(m.HeaderCandidate.model_validate(x) for x in proposal["candidates"])
    decisions = tuple(m.EntryDecision.model_validate(x) for x in proposal["decisions"])
    courses = tuple(m.CourseRecord.model_validate(x["course"]) for x in records)
    headers = {c.candidate_id: c for c in candidates}
    # Reject duplicate cached candidate/decision identity.
    if len(headers) != len(candidates) or len({d.candidate_id for d in decisions}) != len(
        decisions
    ):
        raise ValueError("duplicate cached candidate/decision identity")
    for r, course in zip(records, courses, strict=True):
        page = headers[course.header_candidate_id].physical_page
        # Stop if the cached course scope differs from its bound window.
        if (
            r["dataset_page_id"] != metadata["dataset_page_id"]
            or r["header_physical_page"] != page
            or r["scope"] != ("CENTRAL" if page == metadata["central_physical_page"] else "CONTEXT")
        ):
            raise ValueError("cached course scope differs from its bound window")
    return build_page_accounting(
        graph,
        blocks,
        courses,
        source_graph=source_graph,
        candidates=candidates,
        decisions=decisions,
        table_contexts=table_contexts(layouts),
    )


# Require registered exposed inputs before opening cached native text.
def audit_cached_pages(reference, pilot: Path, evaluation: Path, output: Path) -> dict:
    """Require registered exposed inputs before opening cached native text."""
    _reject_reserved_paths(pilot, evaluation, output)
    metadata = load_exposed_split_metadata(pilot, evaluation, output)
    split_audit = audit_split_metadata(metadata)
    em_path = _metadata_file(evaluation, "evaluation_manifest.json")
    em = read_json(em_path)
    # Stop if the accounting evaluation is not exposed.
    if em["protected_evaluation_opened"] is not False:
        raise ValueError("accounting evaluation is not exposed")
    predictions = Path(em["prediction_path"])
    _reject_reserved_paths(predictions)
    pm_path = _metadata_file(predictions, "prediction_manifest.json")
    # Stop if the accounting prediction manifest changed.
    if sha256(pm_path) != em["prediction_manifest_sha256"]:
        raise ValueError("accounting prediction manifest changed")
    pm = read_json(pm_path)
    # Text accounting must use predictions from the verified source-only sample.
    if (
        pm["source_truth_read"] is not False
        or pm["protected_evaluation_opened"] is not False
        or pm["pilot_manifest_sha256"] != em["pilot_manifest_sha256"]
        or pm["page_windows"] != len(metadata)
    ):
        raise ValueError("accounting predictions differ from bound source-only pilot")
    inputs = {str(em_path): sha256(em_path), str(pm_path): sha256(pm_path)}

    # Check the saved file fingerprint before reading this cached input.
    def pinned(name):
        path = _metadata_file(predictions, name)
        digest = sha256(path)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if digest != pm["file_sha256"].get(name):
            raise ValueError("accounting cached payload checksum changed")
        inputs[str(path)] = digest
        return read_jsonl(path)

    proposals = pinned("page_proposals.jsonl")
    records = pinned("course_proposals.jsonl")
    ids = {x["dataset_page_id"] for x in metadata}
    # Stop if the accounting cache does not cover exactly the bound window inventory.
    if len(proposals) != len(ids) or {x["dataset_page_id"] for x in proposals} != ids:
        raise ValueError("accounting cache does not cover exactly the bound window inventory")
    by_id = {p["dataset_page_id"]: p for p in proposals}
    by_window = defaultdict(list)
    for r in records:
        # Stop if the course cache contains an unregistered window.
        if r["dataset_page_id"] not in ids:
            raise ValueError("course cache contains an unregistered window")
        by_window[r["dataset_page_id"]].append(r)
    ledgers, windows, dispositions = [], [], Counter()
    for item in metadata:
        page_id = item["dataset_page_id"]
        ledger = account_cached_window(reference, item, by_id[page_id], by_window[page_id])
        audit = ledger["audit"]
        dispositions.update(audit["disposition_span_counts"])
        windows.append(
            {
                "dataset_page_id": page_id,
                "learning_partition": item["learning_partition"],
                "evaluation_role": evaluation_role(item["learning_partition"]),
                **audit,
            }
        )
        ledgers.append({"dataset_page_id": page_id, "whole_page_accounting": ledger})
    summary = {
        "schema_version": VERSION,
        "status": "EXTRACTED_SOURCE_RETENTION_PASS",
        "windows": len(windows),
        "role_counts": split_audit["role_counts"],
        **{
            k: sum(w[k] for w in windows)
            for k in (
                "source_cells",
                "source_rows",
                "source_characters",
                "accounted_characters",
                "unresolved_rows",
            )
        },
        "empty_context_page_inventories": sum(
            p["source_inventory_status"] == "EMPTY_SOURCE_INVENTORY"
            for w in windows
            for p in w["pages"]
        ),
        "disposition_span_counts": dict(dispositions),
        "course_records_unchanged": len(records),
        "source_truth_read": False,
        "protected_evaluation_opened": False,
        "semantic_truth_assigned": False,
        "source_extraction_completeness_verified": False,
        "accuracy_improvement_measured": False,
        "limits": LIMITS,
    }
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "page_accounting.jsonl", ledgers)
    write_jsonl(output / "window_audits.jsonl", windows)
    write_json(output / "summary.json", summary)
    _report(output, summary, windows)
    write_json(
        output / "manifest.json",
        {
            "schema_version": VERSION,
            "input_sha256": inputs,
            "pilot_manifest_sha256": sha256(_metadata_file(pilot, "pilot_manifest.json")),
            "selected_metadata_sha256": sha256(_metadata_file(pilot, "selected_pages.jsonl")),
            "source_truth_read": False,
            "protected_evaluation_opened": False,
            "workspace_code_sha256": {
                p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))
            },
            "file_sha256": artifact_hashes(output),
        },
    )
    return summary


# Build the HTML report and escape displayed source text.
def _report(output, summary, windows):
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(w[k]))}</td>"
            for k in (
                "dataset_page_id",
                "evaluation_role",
                "source_cells",
                "source_rows",
                "source_characters",
                "unresolved_rows",
            )
        )
        + "</tr>"
        for w in windows
    )
    limits = "".join(f"<li>{html.escape(x)}</li>" for x in LIMITS)
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Phase 17 · Source accounting</title>"
        "<style>body{font:16px system-ui;margin:40px auto;max-width:1150px;"
        "padding:0 24px;color:#18302c;background:#f6f8f7}"
        "h1{font-size:32px}table{border-collapse:collapse;width:100%;background:white;font-size:13px}"
        "td,th{padding:10px;border-bottom:1px solid #dae3de;text-align:left}"
        "li{margin:10px 0}.stats{font-size:22px}</style>"
        "<h1>Phase 17 · Whole-page accounting</h1><p>Extracted-source retention checks passed. "
        "Semantic classification remains proposed.</p>"
        f'<p class="stats">{summary["windows"]} exposed windows · '
        f"{summary['source_rows']:,} source rows · "
        f"{summary['accounted_characters']:,} characters retained</p>"
        f"<p>{summary['unresolved_rows']:,} unresolved rows remain visible. "
        f"{summary['empty_context_page_inventories']} empty context-page inventories "
        "require source completeness review.</p>"
        f"<ul>{limits}</ul><table><thead><tr><th>Window</th><th>Exposure</th><th>Cells</th>"
        "<th>Rows</th><th>Characters</th><th>Unresolved</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></html>",
        encoding="utf-8",
    )
