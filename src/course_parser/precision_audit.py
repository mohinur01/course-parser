# Measure the fixed review-routing categories against permitted cached examples. Report
# labeled header matches and unknown cases separately; these measurements do not certify
# complete course records.
"""Fixed precision/coverage diagnostics of registered exposed source-only caches."""

from __future__ import annotations

import html
from collections import Counter, defaultdict
from pathlib import Path

from .evaluation_design import _metadata_file, evaluation_role, load_exposed_split_metadata
from .institution_out import bind_records, digest
from .io import artifact_hashes, read_json, read_jsonl, sha256, write_json
from .omission_review import _reject_reserved_paths
from .page_accounting_audit import account_cached_window
from .precision_policy import VERSION, audit_routes, route_window, write_route_outputs

LIMITS = [
    "Bands describe source evidence and core readiness; "
    "they are uncalibrated and do not certify truth.",
    "HIGH permits an existing proposal only. All bands still require human publication review.",
    "No course, source text, boundary, ownership, optional field or parser threshold is changed.",
    "Header metrics are conditional on reviewed generated central candidates. They do not measure "
    "unproposed courses, complete-record precision or corpus recall.",
    "Unknown/ambiguous alignments stay unknown. "
    "Known false courses = 0 does not mean perfect records.",
    "Coverage is a share of existing emitted central proposals, "
    "not recovery of all source courses.",
    "Exposed, selected and overlapping windows cannot establish untouched institutional transfer.",
    "Cached labels are opened only after fixed source-only routing. "
    "No calibration or threshold search.",
    "The reserved cohort stays sealed. "
    "This command neither freezes the parser nor opens evaluation.",
]


# Two predeclared nested selections; unknown labels never become negatives or positives.
def coverage_metrics(routes, examples):
    """Two predeclared nested selections; unknown labels never become negatives or positives."""
    central = [r for r in routes if r["scope"] == "CENTRAL"]
    by_key = {(r["dataset_page_id"], r["candidate_id"]): r for r in central}
    labels = {(x["dataset_page_id"], x["candidate_id"]): x["label"] for x in examples}
    # Reject duplicate precision metric identity.
    if len(by_key) != len(central) or len(labels) != len(examples):
        raise ValueError("duplicate precision metric identity")
    # Stop if the precision labels do not match generated central candidates.
    if not labels.keys() <= by_key.keys() or any(
        type(x) is not int or x not in (0, 1) for x in labels.values()
    ):
        raise ValueError("precision labels do not match generated central candidates")
    total = sum(r["existing_record_retained"] for r in central)
    positives = sum(labels.values())
    results = []
    for name, bands in (("HIGH_ONLY", {"HIGH"}), ("HIGH_PLUS_MEDIUM", {"HIGH", "MEDIUM"})):
        selected = {
            key for key, r in by_key.items() if r["existing_record_retained"] and r["band"] in bands
        }
        tp = sum(labels.get(key) == 1 for key in selected)
        fp = sum(labels.get(key) == 0 for key in selected)
        unknown = len(selected - labels.keys())
        results.append(
            {
                "selection": name,
                "existing_central_proposals": len(selected),
                "proposal_coverage": len(selected) / total if total else None,
                "reviewed_header_true_positives": tp,
                "reviewed_header_false_positives": fp,
                "unlabeled_or_ambiguous_proposals": unknown,
                "reviewed_header_precision": tp / (tp + fp) if tp + fp else None,
                "observed_positive_share_of_all_selected": tp / len(selected) if selected else None,
                "conditional_reviewed_header_recall": tp / positives if positives else None,
                "reviewed_generated_positive_denominator": positives,
            }
        )
    return results


# Measure the predeclared review categories on approved cached examples, leaving unknown
# alignments unknown.
def audit_precision(reference, pilot: Path, evaluation: Path, output: Path) -> dict:
    _reject_reserved_paths(pilot, evaluation, output)
    metadata = load_exposed_split_metadata(pilot, evaluation, output)
    em_path = _metadata_file(evaluation, "evaluation_manifest.json")
    em = read_json(em_path)
    predictions = Path(em["prediction_path"])
    _reject_reserved_paths(predictions)
    pm_path = _metadata_file(predictions, "prediction_manifest.json")
    # Stop if the precision prediction manifest changed.
    if sha256(pm_path) != em["prediction_manifest_sha256"]:
        raise ValueError("precision prediction manifest changed")
    pm = read_json(pm_path)
    # Review routing must use the registered source-only sample and its matching cached
    # predictions.
    if (
        em["protected_evaluation_opened"] is not False
        or pm["source_truth_read"] is not False
        or pm["protected_evaluation_opened"] is not False
        or pm["pilot_manifest_sha256"] != em["pilot_manifest_sha256"]
        or pm["page_windows"] != len(metadata)
    ):
        raise ValueError("precision inputs differ from the registered source-only pilot")
    inputs = {str(em_path): sha256(em_path), str(pm_path): sha256(pm_path)}

    # Check the saved file fingerprint before reading this cached input.
    def pinned(root, manifest, name):
        path = _metadata_file(root, name)
        value = sha256(path)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if value != manifest["file_sha256"].get(name):
            raise ValueError("precision cached payload checksum changed")
        inputs[str(path)] = value
        return read_jsonl(path)

    proposals = pinned(predictions, pm, "page_proposals.jsonl")
    records = pinned(predictions, pm, "course_proposals.jsonl")
    ids = {x["dataset_page_id"] for x in metadata}
    # Stop if the precision cache does not cover exactly the registered windows.
    if len(proposals) != len(ids) or {p["dataset_page_id"] for p in proposals} != ids:
        raise ValueError("precision cache does not cover exactly the registered windows")
    by_id = {p["dataset_page_id"]: p for p in proposals}
    by_window = defaultdict(list)
    for r in records:
        # Route only courses belonging to the registered page selections.
        if r["dataset_page_id"] not in ids:
            raise ValueError("precision course outside registered windows")
        by_window[r["dataset_page_id"]].append(r)
    routes, failures = [], []
    for item in metadata:
        window = item["dataset_page_id"]
        p = by_id[window]
        accounting = account_cached_window(reference, item, p, by_window[window])
        values = route_window(item, p, by_window[window], accounting)
        audit_routes(item, p, by_window[window], accounting, values)
        routes.extend(values)
        if p["status"] == "PARSER_STAGE_FAILED":
            failures.append(
                {"dataset_page_id": window, "review_required": True, "status": p["status"]}
            )
    # This digest is formed before reading any candidate labels; routing cannot use answers.
    routing_digest = digest(routes)
    pages = pinned(evaluation, em, "page_metrics.jsonl")
    examples = pinned(evaluation, em, "entry_training_examples.jsonl")
    split = bind_records(metadata, pages, examples)
    # Stop if the candidate labels changed source-only precision routing.
    if digest(routes) != routing_digest:
        raise ValueError("candidate labels changed source-only precision routing")
    roles = {}
    scored_ids = {p["dataset_page_id"] for p in pages if p["status"] == "SCORED_DEVELOPMENT"}
    for role in ("TUNING", "EXPOSED_DIAGNOSTIC"):
        role_ids = {
            x["dataset_page_id"]
            for x in metadata
            if evaluation_role(x["learning_partition"]) == role
        }
        roles[role] = coverage_metrics(
            [r for r in routes if r["dataset_page_id"] in role_ids],
            [x for x in examples if x["dataset_page_id"] in role_ids],
        )
    metric_keys = (
        "central_course_proposals",
        "matched_course_proposals",
        "false_course_proposals",
        "ambiguous_course_proposals",
        "description_contamination",
        "complete_content_correct",
        "complete_content_eligible",
        "complete_all_fields_correct",
        "complete_all_fields_eligible",
    )
    summary = {
        "schema_version": VERSION,
        "status": "REVIEW_ROUTING_READY",
        "windows": len(metadata),
        "role_counts": split["role_counts"],
        "bands_context_and_central": dict(Counter(r["band"] for r in routes)),
        "bands_central": dict(Counter(r["band"] for r in routes if r["scope"] == "CENTRAL")),
        "source_only_routing_sha256": routing_digest,
        "existing_records_retained": len(records),
        "automatic_publication_authorized": False,
        "measured_extraction_accuracy_gain": False,
        "protected_evaluation_opened": False,
        "routing_source_truth_read": False,
        "cached_exposed_candidate_labels_read_for_measurement": True,
        "precision_coverage": coverage_metrics(routes, examples),
        "scored_window_precision_coverage": coverage_metrics(
            [r for r in routes if r["dataset_page_id"] in scored_ids], examples
        ),
        "page_review_status_counts": dict(Counter(p["status"] for p in pages)),
        "by_role": roles,
        "unchanged_cached_strict_course_metrics": {
            k: em["overall"][k] for k in metric_keys if k in em["overall"]
        },
        "window_failures": len(failures),
        "limits": LIMITS,
    }
    output.mkdir(parents=True, exist_ok=False)
    write_route_outputs(output, routes, failures)
    write_json(output / "summary.json", summary)
    _report(output, summary)
    write_json(
        output / "manifest.json",
        {
            "schema_version": VERSION,
            "input_sha256": inputs,
            "pilot_manifest_sha256": sha256(_metadata_file(pilot, "pilot_manifest.json")),
            "selected_metadata_sha256": sha256(_metadata_file(pilot, "selected_pages.jsonl")),
            "routing_source_truth_read": False,
            "cached_exposed_candidate_labels_read_for_measurement": True,
            "protected_evaluation_opened": False,
            "automatic_publication_authorized": False,
            "workspace_code_sha256": {
                p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))
            },
            "file_sha256": artifact_hashes(output),
        },
    )
    return summary


# Build the HTML report and escape displayed source text.
def _report(output, summary):
    # Build an HTML table and escape the text in its cells.
    def table(rows):
        return (
            "<table><tr>"
            + "".join(f"<th>{html.escape(k.replace('_', ' '))}</th>" for k in rows[0])
            + "</tr>"
            + "".join(
                "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r.values()) + "</tr>"
                for r in rows
            )
            + "</table>"
        )

    columns = (
        "selection",
        "existing_central_proposals",
        "proposal_coverage",
        "reviewed_header_false_positives",
        "unlabeled_or_ambiguous_proposals",
        "reviewed_header_precision",
    )
    rows = [{k: r[k] for k in columns} for r in summary["scored_window_precision_coverage"]]
    body = (
        "<h1>Phase 18 · Protect precision</h1><p>Source-only proposal bands. "
        "Every course still requires human publication review.</p>"
    )
    body += "<h2>Central proposals in source-reviewed windows</h2>" + table(rows)
    body += "<p>Unknown alignments remain unknown. Header precision does not certify fields.</p>"
    body += "<h2>Unchanged strict course results</h2>" + table(
        [summary["unchanged_cached_strict_course_metrics"]]
    )
    body += "<h2>All exposed windows, including pending source review</h2>" + table(
        [{k: r[k] for k in columns} for r in summary["precision_coverage"]]
    )
    body += (
        "<p>Window review status: "
        + html.escape(str(summary["page_review_status_counts"]))
        + "</p>"
    )
    for role, metrics in summary["by_role"].items():
        body += f"<h2>{role}</h2>" + table([{k: r[k] for k in columns} for r in metrics])
    body += (
        "<h2>Interpretation limits</h2><ul>"
        + "".join(f"<li>{html.escape(x)}</li>" for x in LIMITS)
        + "</ul>"
    )
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Phase 18 precision</title>'
        "<style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 24px;"
        "background:#f6f8f7;color:#18302c}table{border-collapse:collapse;width:100%;font-size:13px;"
        "background:white}td,th{padding:10px;text-align:left;border-bottom:1px solid #dae3de}"
        "li{margin:10px 0}h2{margin-top:32px}</style>" + body + "</html>",
        encoding="utf-8",
    )
