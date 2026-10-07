# Purpose: Prepare and verify bounded omission-review evidence while keeping all review decisions
# pending.
# ruff: noqa: E501 -- HTML fragments and embedded CSS
"""Bounded omission/crop audit and a local, pending source-review inbox."""

from __future__ import annotations

import html
import os
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from .crop_extractions import extraction_disagreements, render_crop, run_tesseract
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
from .layout_models import Box
from .learning import verify_learning_pilot
from .ocr_recovery import VERSION as OCR_RECOVERY_VERSION
from .ocr_recovery import reconcile_crop, route_crop
from .omission_ranking import VERSION as RANKING_VERSION
from .omission_ranking import group_duplicate_flags, priority_sort_key
from .omission_signals import VERSION, audit_page


# Reject named sealed inputs before any lookup of those inputs.
def _reject_reserved_paths(*arguments):
    """Reject named sealed inputs before any lookup of those inputs."""
    workspace = Path(__file__).resolve().parents[2]
    policy = read_json(workspace / "config/development_phase_execution_v1.json")
    # Omission audit must retain the 18-phase reserved-cohort seal.
    if policy["reserved_cohort_access"] != "SEALED" or policy["total_phases"] != 18:
        raise ValueError("omission audit must retain the 18-phase reserved-cohort seal")
    blocked = [workspace / path for path in policy["blocked_paths"]]
    for argument in arguments:
        lexical = Path(os.path.abspath(argument))
        # Keep reserved evaluation data out of this development workflow.
        if any(lexical == root or lexical.is_relative_to(root) for root in blocked):
            raise ValueError("reserved cohort access is forbidden during these phases")
        resolved = lexical.resolve()
        # Keep reserved evaluation data out of this development workflow.
        if any(resolved == root or resolved.is_relative_to(root) for root in blocked):
            raise ValueError("reserved cohort access is forbidden during these phases")


# Reject reserved paths, verify the source-only prediction manifest and check its pilot and
# file checksums.
def _verified_predictions(pilot, predictions):
    _reject_reserved_paths(pilot, predictions)
    verify_learning_pilot(pilot)
    manifest = read_json(predictions / "prediction_manifest.json")
    # Omission audit requires source-only predictions.
    if (
        manifest.get("source_truth_read") is not False
        or manifest.get("protected_evaluation_opened") is not False
    ):
        raise ValueError("omission audit requires source-only predictions")
    # Stop if the omission audit prediction cohort changed.
    if manifest["pilot_manifest_sha256"] != sha256(pilot / "pilot_manifest.json"):
        raise ValueError("omission audit prediction cohort changed")
    # Omission audit requires a DEVELOPMENT pilot.
    if read_json(pilot / "pilot_manifest.json").get("split") != "DEVELOPMENT":
        raise ValueError("omission audit requires a DEVELOPMENT pilot")
    verify_files(predictions, manifest["file_sha256"])
    return manifest


# Round robin signal families and pages, using source signals only.
def select_crop_flags(flags: list[dict], limit: int) -> list[dict]:
    """Round robin signal families and pages, using source signals only."""
    # Crop limit must be positive.
    if limit < 1:
        raise ValueError("crop limit must be positive")
    buckets = defaultdict(list)
    for flag in flags:
        if flag["learning_partition"] != "TUNING":
            continue
        family = next(
            (
                s
                for s in (
                    "HISTORICAL_IDENTIFIER",
                    "HEADER_WITHOUT_SUBJECT_CODE",
                    "UNCOVERED_DESCRIPTION_BLOCK",
                    "MODERN_IDENTIFIER",
                )
                if s in flag["signals"]
            ),
            "OTHER",
        )
        buckets[family].append(flag)
    for bucket in buckets.values():
        bucket.sort(key=priority_sort_key)
    chosen, used_pages = [], Counter()
    # Rotate through signal families and favor less-used pages so the crop budget
    # covers a range of possible omissions.
    while len(chosen) < limit and any(buckets.values()):
        for family in sorted(buckets):
            if buckets[family] and len(chosen) < limit:
                flag = min(
                    buckets[family],
                    key=lambda f: (
                        used_pages[f["dataset_page_id"]],
                        priority_sort_key(f),
                    ),
                )
                buckets[family].remove(flag)
                chosen.append(flag)
                used_pages[flag["dataset_page_id"]] += 1
    return chosen


# Choose a bounded set of omission flags and save their source crops in a review packet.
def prepare_omission_review(
    pilot: Path, predictions: Path, output: Path, *, crop_limit=12, dpi=300, tesseract="tesseract"
):
    _reject_reserved_paths(pilot, predictions, output)
    _verified_predictions(pilot, predictions)
    pages = {p["dataset_page_id"]: p for p in read_jsonl(pilot / "selected_pages.jsonl")}
    flags, statuses, copied, layouts = [], [], {}, {}
    output.mkdir(parents=True, exist_ok=False)
    (output / "source_pages").mkdir()
    for proposal in read_jsonl(predictions / "page_proposals.jsonl"):
        selected = pages[proposal["dataset_page_id"]]
        packet = contained_file(pilot, selected["packet_path"])
        source = read_json(packet / "source_reference.json")
        for asset in source["page_artifacts"]:
            physical = asset["physical_page"]
            key = (selected["dataset_page_id"], physical)
            name = f"{selected['dataset_page_id']}_p{physical}.png"
            destination = output / "source_pages" / name
            shutil.copyfile(asset["image_path"], destination)
            # Stop if the source image changed while copying review evidence.
            if sha256(destination) != asset["image_sha256"]:
                raise ValueError("source image changed while copying review evidence")
            copied[key] = str(destination.relative_to(output))
            text_destination = (
                output
                / "source_pages"
                / f"{selected['dataset_page_id']}_p{physical}.source_text.json"
            )
            shutil.copyfile(asset["source_text_path"], text_destination)
            # Stop if the frozen source text changed while copying review evidence.
            if sha256(text_destination) != asset["source_text_sha256"]:
                raise ValueError("frozen source text changed while copying review evidence")
        if proposal["status"] != "PROPOSED":
            statuses.append(
                {
                    "dataset_page_id": selected["dataset_page_id"],
                    "status": "SOURCE_EVIDENCE_INCOMPLETE",
                    "reason": proposal["status"],
                }
            )
            continue
        layout = next(
            entry
            for entry in proposal["layouts"]
            if entry["physical_page"] == selected["central_physical_page"]
        )
        layouts[(selected["dataset_page_id"], layout["physical_page"])] = layout
        # Stop if the omission layout source identity changed.
        if (
            layout["source_pdf_checksum"] != selected["source_pdf_checksum"]
            or layout["source_document_id"] != selected["source_document_id"]
        ):
            raise ValueError("omission layout source identity changed")
        asset = next(
            a
            for a in source["page_artifacts"]
            if a["physical_page"] == selected["central_physical_page"]
        )
        binding = {
            k: selected[k]
            for k in (
                "dataset_page_id",
                "source_document_id",
                "source_pdf_checksum",
                "learning_group",
                "learning_partition",
            )
        }
        binding.update(
            {
                "geometry_sha256": asset["embedded_geometry_sha256"],
                "source_image_sha256": asset["image_sha256"],
                "frozen_source_text_sha256": asset["source_text_sha256"],
                "frozen_source_text_path": f"source_pages/{selected['dataset_page_id']}_p{selected['central_physical_page']}.source_text.json",
                "original_page_task_id": selected.get("original_page_task_id"),
                "source_page_image": copied[
                    (selected["dataset_page_id"], selected["central_physical_page"])
                ],
                "context_images": [
                    copied[(selected["dataset_page_id"], p)]
                    for p in selected["context_physical_pages"]
                ],
            }
        )
        page_flags = audit_page(
            read_json(Path(asset["embedded_geometry_path"])), layout, proposal, binding
        )
        flags.extend(page_flags)
        statuses.append(
            {
                "dataset_page_id": selected["dataset_page_id"],
                "status": "AUDITED",
                "flags": len(page_flags),
            }
        )
    # Reject duplicate physical omission flag.
    if len({f["flag_id"] for f in flags}) != len(flags):
        raise ValueError("duplicate physical omission flag")
    write_jsonl(output / "omission_flags.jsonl", flags)
    groups = group_duplicate_flags(flags)
    write_jsonl(output / "omission_review_groups.jsonl", groups)
    write_jsonl(output / "page_audit_status.jsonl", statuses)
    chosen = select_crop_flags(groups, crop_limit)
    materialized, paths, ocr_paths = [], [], []
    for flag in chosen:
        selected = pages[flag["dataset_page_id"]]
        # Include nearby prose as context, not an asserted course boundary.
        boxes = [flag["geometry"], *[entry["geometry"] for entry in flag["nearby_source_lines"]]]
        geometry = read_json(
            next(
                Path(a["embedded_geometry_path"])
                for a in read_json(
                    contained_file(pilot, selected["packet_path"]) / "source_reference.json"
                )["page_artifacts"]
                if a["physical_page"] == flag["physical_page"]
            )
        )
        box = Box(
            x0=max(0, min(b[0] for b in boxes) - 8),
            y0=max(0, flag["geometry"][1] - 8),
            x1=min(geometry["canonical_width_points"], max(b[2] for b in boxes) + 8),
            y1=min(geometry["canonical_height_points"], max(b[3] for b in boxes) + 8),
        )
        relative = f"crops/{flag['flag_id']}"
        root = output / relative
        try:
            render_crop(
                Path(selected["source_pdf_path"]),
                selected["source_pdf_checksum"],
                flag["physical_page"],
                box,
                root,
                dpi=dpi,
            )
            context = {
                "layout": layouts[(flag["dataset_page_id"], flag["physical_page"])],
                "omission_kind": flag["kind"],
            }
            write_json(root / "ocr_context.json", context)
            routing = route_crop(root, **context)
            write_json(root / "ocr_routing.json", routing)
            if routing["ocr_eligible"]:
                for mode in (6, 11):
                    run_tesseract(root, psm=mode, executable=tesseract)
                ocr_paths.append(relative)
            paths.append(relative)
            materialized.append(
                {
                    "flag_id": flag["flag_id"],
                    "crop_path": relative,
                    "status": "MATERIALIZED",
                    "error": None,
                    "ocr_routing": routing["decision"],
                }
            )
        # Handle oserror, value error, runtime error by keeping the failure explicit or using
        # the documented fallback.
        except (OSError, ValueError, RuntimeError) as exc:
            materialized.append(
                {
                    "flag_id": flag["flag_id"],
                    "crop_path": relative,
                    "status": "SOURCE_EVIDENCE_INCOMPLETE",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    write_jsonl(output / "crop_status.jsonl", materialized)
    manifest = {
        "schema_version": "source-omission-preparation-v2",
        "signal_version": VERSION,
        "ranking_version": RANKING_VERSION,
        "pilot_path": str(pilot.resolve()),
        "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        "predictions_path": str(predictions.resolve()),
        "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
        "windows": len(statuses),
        "flags": len(flags),
        "review_groups": len(groups),
        "duplicate_observations_grouped": len(flags) - len(groups),
        "all_physical_flags_retained": True,
        "priority_counts": dict(Counter(g["priority_tier"] for g in groups)),
        "priority_weights_calibrated": False,
        "crop_limit": crop_limit,
        "crop_paths": paths,
        "ocr_recovery_version": OCR_RECOVERY_VERSION,
        "ocr_eligible_crop_paths": ocr_paths,
        "ocr_executions_requested": 2 * len(ocr_paths),
        "ocr_routing_counts": dict(
            Counter(r.get("ocr_routing", "FAILED_CROP") for r in materialized)
        ),
        "crop_selection_uses_answers": False,
        "crop_partition": "TUNING",
        "source_truth_read": False,
        "protected_evaluation_opened": False,
        "original_hit_dispositions_changed": False,
        "workspace_code_sha256": {
            str(p.name): sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))
        },
        "file_sha256": artifact_hashes(output),
    }
    write_json(output / "preparation_manifest.json", manifest)
    return {
        "status": "SOURCE_REVIEW_PREPARED",
        "windows": len(statuses),
        "flags": len(flags),
        "review_groups": len(groups),
        "crops_materialized": len(paths),
        "crop_failures": sum(r["status"] != "MATERIALIZED" for r in materialized),
        "ocr_eligible_crops": len(ocr_paths),
    }


# Check the completed bounded audit files and write the manifest for the pending
# omission-review inbox.
def finalize_omission_review(run: Path):
    _reject_reserved_paths(run)
    run = run.resolve()
    preparation = read_json(run / "preparation_manifest.json")
    verify_files(run, preparation["file_sha256"])
    # Stop if the source-review preparation policy changed.
    if (
        preparation["source_truth_read"] is not False
        or preparation["protected_evaluation_opened"] is not False
    ):
        raise ValueError("source-review preparation policy changed")
    # Stop if the audit code changed after preparation; prepare a fresh review run.
    if preparation["workspace_code_sha256"] != {
        str(path.name): sha256(path) for path in sorted(Path(__file__).parent.glob("*.py"))
    }:
        raise ValueError("audit code changed after preparation; prepare a fresh review run")
    crop_status = {c["flag_id"]: c for c in read_jsonl(run / "crop_status.jsonl")}
    queue, comparisons = [], []
    group_file = (
        "omission_review_groups.jsonl"
        if preparation.get("review_groups") is not None
        else "omission_flags.jsonl"
    )
    for flag in read_jsonl(run / group_file):
        materialized = crop_status.get(flag["flag_id"])
        comparison = None
        if materialized and materialized["status"] == "MATERIALIZED":
            crop = contained_file(run, materialized["crop_path"])
            comparison = extraction_disagreements(crop)
            if (crop / "ocr_routing.json").exists():
                context = read_json(crop / "ocr_context.json")
                # Stop if the OCR source routing changed after preparation.
                if route_crop(crop, **context) != read_json(crop / "ocr_routing.json"):
                    raise ValueError("OCR source routing changed after preparation")
                recovery = reconcile_crop(crop, **context)
                write_json(crop / "ocr_reconciliation.json", recovery)
                comparison = {**recovery["comparisons"], "ocr_recovery": recovery}
            comparisons.append({"flag_id": flag["flag_id"], **comparison})
        queue.append(
            {
                "review_task_id": flag.get("review_group_id", flag["flag_id"]),
                "flag": flag,
                "crop": materialized,
                "review_reasons": [
                    {"kind": flag["kind"]},
                    *([] if comparison is None else comparison["review_events"]),
                ],
                "extraction_comparison": comparison,
                "review_status": "SOURCE_EVIDENCE_INCOMPLETE"
                if materialized and materialized["status"] != "MATERIALIZED"
                else "PENDING_SOURCE_REVIEW",
                "disposition": None,
                "reviewer": None,
                "adjudicator": None,
                "source_review_notes": None,
                "course_truth_assigned": False,
            }
        )
    # Stage failures get explicit tasks even if there is no discoverable header.
    for status in read_jsonl(run / "page_audit_status.jsonl"):
        if status["status"] != "AUDITED":
            queue.append(
                {
                    "review_task_id": "source-incomplete-" + status["dataset_page_id"],
                    "dataset_page_id": status["dataset_page_id"],
                    "review_reasons": [status],
                    "review_status": "SOURCE_EVIDENCE_INCOMPLETE",
                    "disposition": None,
                    "reviewer": None,
                    "course_truth_assigned": False,
                }
            )
    write_jsonl(run / "review_queue.jsonl", queue)
    write_jsonl(run / "extraction_comparisons.jsonl", comparisons)
    write_json(
        run / "review_contract.json",
        {
            "allowed_dispositions": [
                "COURSE_OMITTED",
                "EXTRACTION_ERROR",
                "NONCOURSE",
                "ALREADY_COVERED",
                "SOURCE_INPUT_INSUFFICIENT",
            ],
            "instructions": [
                "Inspect the central source page first. Crops are diagnostic context, not course boundaries.",
                "Use neighboring pages only for continuations and boundary decisions.",
                "Inspect each engine output separately. Agreement is not independent course truth.",
                "Record the printed identifier/title and exact owned source extent if a course was omitted.",
                "Review references/requirements as hard negatives. No explicit credit is required for a course.",
                "Do not repair OCR characters or expand the frozen vocabulary.",
                "Do not update original keyword-hit dispositions from this diagnostic inbox.",
                "Missing evidence remains SOURCE_INPUT_INSUFFICIENT. Labels are blank until source review.",
                "Priority points are provisional review order, not confidence probabilities or course decisions.",
                "Review low-priority conflicts too; all physical observations and duplicate aliases are retained.",
            ],
            "external_messages_sent": False,
            "training_labels_created": 0,
        },
    )
    write_review_viewer(run, queue)
    counts = Counter(event["kind"] for c in comparisons for event in c["review_events"])
    manifest = {
        "schema_version": "source-omission-review-v2",
        "status": "PENDING_SOURCE_REVIEW",
        "preparation_manifest_sha256": sha256(run / "preparation_manifest.json"),
        "windows": preparation["windows"],
        "omission_flags": preparation["flags"],
        "review_groups": preparation.get("review_groups", preparation["flags"]),
        "duplicate_observations_grouped": preparation.get("duplicate_observations_grouped", 0),
        "priority_counts": preparation.get("priority_counts", {}),
        "crop_comparisons": len(comparisons),
        "review_tasks": len(queue),
        "disagreement_crop_count": sum(
            any(e["kind"] == "TEXT_DISAGREEMENT" for e in c["review_events"]) for c in comparisons
        ),
        "review_event_counts": dict(counts),
        "source_truth_read": False,
        "protected_evaluation_opened": False,
        "accepted_courses_added": 0,
        "new_keyword_hits_created": 0,
        "file_sha256": artifact_hashes(run),
    }
    write_json(run / "review_manifest.json", manifest)
    return {
        k: manifest[k]
        for k in (
            "status",
            "windows",
            "omission_flags",
            "crop_comparisons",
            "review_tasks",
            "disagreement_crop_count",
            "review_event_counts",
            "accepted_courses_added",
        )
    }


# Check that the omission packet still matches its saved source files, crops and unanswered
# review contract.
def verify_omission_review(run: Path):
    _reject_reserved_paths(run)
    run = run.resolve()
    manifest = read_json(run / "review_manifest.json")
    verify_files(run, manifest["file_sha256"])
    preparation = read_json(run / "preparation_manifest.json")
    # Stop if the omission review preparation changed.
    if sha256(run / "preparation_manifest.json") != manifest["preparation_manifest_sha256"]:
        raise ValueError("omission review preparation changed")
    for key in ("pilot", "prediction"):
        base = Path(preparation["pilot_path" if key == "pilot" else "predictions_path"])
        _reject_reserved_paths(base)
        # Stop if the omission review upstream manifest changed.
        if sha256(base / f"{key}_manifest.json") != preparation[f"{key}_manifest_sha256"]:
            raise ValueError("omission review upstream manifest changed")
    for crop in preparation["crop_paths"]:
        extraction_disagreements(contained_file(run, crop))
    return {
        "status": "VERIFIED",
        "review_tasks": manifest["review_tasks"],
        "accepted_courses_added": 0,
    }


# Write the omission-review viewer with the source crop and each separate extraction reading.
def write_review_viewer(run: Path, queue: list[dict]):
    run = run.resolve()
    esc = html.escape
    css = "body{font:17px system-ui;max-width:1250px;margin:32px auto;padding:0 20px;color:#23313f;background:#f5f7fa}a{color:#075fa6}article{background:white;border:1px solid #cad5df;padding:22px;margin:20px 0;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px monospace}img{max-width:100%;height:auto}table{width:100%;border-collapse:collapse;table-layout:fixed}td,th{border:1px solid #cad5df;padding:9px;vertical-align:top;overflow-wrap:anywhere}.columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}.badge{color:#7a3b00;background:#fff0d5;padding:4px 8px}details{margin:16px 0}"

    # Build the HTML report and escape displayed source text.
    def document(title, body):
        return f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style><body>{body}</body></html>"

    (run / "tasks").mkdir(exist_ok=False)
    rows = []
    for task in queue:
        flag = task.get("flag")
        if flag is None:
            rows.append(
                f"<tr><td>{esc(task['dataset_page_id'])}</td><td>{esc(str(task['review_reasons']))}</td><td>Missing evidence</td><td>SOURCE_EVIDENCE_INCOMPLETE</td></tr>"
            )
            continue
        filename = task["review_task_id"] + ".html"
        label = flag["raw_text"]
        score = flag.get("omission_score")
        tier = flag.get("priority_tier", flag["review_priority"])
        reasons = "".join(
            f"<li>{reason['points']:+d} {esc(reason['reason_code'].lower().replace('_', ' '))}</li>"
            for reason in flag.get("priority_reasons", [])
        )
        aliases = flag.get("member_flag_ids", [flag["flag_id"]])
        priority = f"<article><h2>Review priority: {esc(tier)} · {score if score is not None else 'unscored'} points</h2><p>Provisional review order; not a probability or a course decision. Low-priority flags remain available.</p><ul>{reasons}</ul><p>{len(aliases)} physical observation(s) retained.</p><details><summary>Original flag IDs</summary><pre>{esc(str(aliases))}</pre></details></article>"
        crop = task["crop"]
        records_html, crop_html = (
            "",
            "<p>Crop not selected in this bounded TUNING sample. Full-page evidence is available.</p>",
        )
        if crop and crop["status"] == "MATERIALIZED":
            root = contained_file(run, crop["crop_path"])
            crop_html = f"<img src='../{esc(crop['crop_path'])}/crop.png' alt='Diagnostic source crop'><p>This crop includes surrounding text. It does not assert a course boundary.</p>"
            for record_path in sorted(root.glob("*/extraction.json")):
                record = read_json(record_path)
                relative = str(record_path.relative_to(run))
                records_html += f"<article><h3>{esc(record_path.parent.name)}</h3><p>{esc(record['status'])} · {esc(str(record['engine_version']))}</p><pre>{esc(record['raw_text'] or '')}</pre><p>{esc(str(record['review_flags']))}</p><p>{esc(record.get('error') or '')}</p><a href='../{esc(relative)}'>Separate extraction record</a></article>"
            transform = read_json(root / "transform.json")
            crop_html += f"<details><summary>Crop transforms and input checksums</summary><pre>{esc(str(transform))}</pre><a href='../{esc(crop['crop_path'])}/transform.json'>Transform JSON</a></details>"
            if (root / "ocr_reconciliation.json").exists():
                recovery = read_json(root / "ocr_reconciliation.json")
                crop_html += f"<p>OCR routing: {esc(recovery['route']['decision'])}. Evidence decision: {esc(recovery['decision'])}.</p><p>Native and OCR readings remain separate. Proposed corrections require source review.</p><a href='../{esc(crop['crop_path'])}/ocr_reconciliation.json'>Recovery evidence</a>"
        elif crop:
            crop_html = f"<p>Source evidence incomplete: {esc(crop['error'])}</p>"
        comparison = task["extraction_comparison"]
        pairs = (
            ""
            if comparison is None
            else "".join(
                f"<tr><td>{esc(' ↔ '.join(p['extraction_ids']))}</td><td>{esc(p['status'].lower().replace('_', ' '))}</td><td>{'Different engines' if p['independent_engines'] else 'Same engine, different settings'}</td></tr>"
                for p in comparison["pairs"]
            )
        )
        images = "".join(
            f"<details><summary>Context page {esc(Path(path).stem.split('_p')[-1])}</summary><img src='../{esc(path)}' alt='Context page'></details>"
            for path in flag["context_images"]
            if path != flag["source_page_image"]
        )
        frozen = read_json(contained_file(run, flag["frozen_source_text_path"]))
        images += f"<details><summary>Separate frozen page text</summary><pre>{esc(frozen.get('raw_text') or '')}</pre><a href='../{esc(flag['frozen_source_text_path'])}'>Original source-text JSON</a><p>Frozen text has not been replaced or automatically aligned to OCR.</p></details>"
        body = f"<a href='../index.html'>Review inbox</a><h1>{esc(label)}</h1><p class='badge'>{esc(task['review_status'])}</p>{priority}<p>{esc(flag['kind'].lower().replace('_', ' '))} · {esc(flag['learning_partition'])} · physical page {flag['physical_page']}</p><p>Signals: {esc(', '.join(flag['signals']))}<br>Source cues: {esc(', '.join(flag['corroborating_cues']))}</p><article><h2>Central source page</h2><p>Inspect this page first to decide whether this is an independent course.</p><img src='../{esc(flag['source_page_image'])}' alt='Central source page'>{images}</article><article><h2>Diagnostic crop</h2>{crop_html}</article><h2>Separate extraction outputs</h2><div class='columns'>{records_html}</div><article><h2>Disagreements routed to review</h2><table><tr><th>Outputs</th><th>Comparison</th><th>Independence</th></tr>{pairs}</table><pre>{esc(str(task['review_reasons']))}</pre></article><article><h2>Pending source decision</h2><p>Disposition, reviewer, owned course boundary, and notes remain blank. Use review_queue.jsonl and review_contract.json to record reviewed evidence. No decision here changes the authoritative hit ledger.</p><pre>{esc(str({k: flag[k] for k in ('flag_id', 'source_document_id', 'source_pdf_checksum', 'node_id', 'geometry_sha256')}))}</pre></article>"
        (run / "tasks" / filename).write_text(document(label, body), encoding="utf-8")
        rows.append(
            f"<tr><td><a href='tasks/{esc(filename)}'>{esc(label)}</a></td><td>{esc(', '.join(flag['signals']).lower().replace('_', ' '))}</td><td>{esc(tier)} · {score if score is not None else 'unscored'} points<br>{esc(flag['review_priority'].lower().replace('_', ' '))}<br>{len(aliases)} observation(s)</td><td>{esc(task['review_status'])}</td></tr>"
        )
    body = f"<h1>Possible omissions and extraction disagreements</h1><p class='badge'>DEVELOPMENT · pending source review</p><p>Scores order review questions and are not probabilities. All priority tiers remain available; duplicate aliases retain their original evidence. Independent source signals raise questions for review. Crops retain native text, Tesseract settings, and optional PP-Structure output separately. Agreement does not establish a course.</p><p>{len(queue)} pending tasks · <a href='review_queue.jsonl'>Blank review queue</a> · <a href='review_contract.json'>Review contract</a> · <a href='extraction_comparisons.jsonl'>Extraction comparisons</a></p><table><tr><th>Source header or prose</th><th>Signals</th><th>Priority</th><th>Status</th></tr>{''.join(rows)}</table>"
    (run / "index.html").write_text(
        document("Course parser source-review inbox", body), encoding="utf-8"
    )
