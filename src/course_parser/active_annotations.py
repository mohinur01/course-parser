# Choose which permitted pages to annotate next. The selection mixes difficult cases, less
# common catalog styles and random examples; reviewers fill in the answers afterward.
"""Mixed TUNING annotation selection; proposals and scores never become gold labels."""

from __future__ import annotations

import html
import random
import re
import shutil
from collections import Counter
from pathlib import Path

from .evaluation_design import _metadata_file, audit_split_metadata, load_exposed_split_metadata
from .io import artifact_hashes, read_json, read_jsonl, sha256, write_json, write_jsonl
from .omission_review import _reject_reserved_paths
from .sequence_annotations import audit_sequence_reviews, source_order_digest

VERSION = "course-parser-active-annotation-v1"
STREAMS = ("FAILURES", "STYLE_COVERAGE", "RANDOM")
DEFAULT_SHARES = (40, 30, 30)
CHECKLIST = [
    "Distinguish course headers from references and requirement lists.",
    "Check historical numeric, lettered and Roman identifiers.",
    "Check full descriptions, including long paragraphs and metadata transitions.",
    "Record printed credits or source-reviewed absence; missing proposals are not absence.",
    "Check tables and independent columns against the page images.",
    "Check continuation across columns/pages and the whole context window.",
    "Preserve damaged OCR characters; mark insufficient source rather than guessing.",
    "Look for title-only headers even when the parser proposed no candidate.",
    "Label every source character, including furniture, references and noncourse text.",
]
LIMITS = [
    "40/30/30 is a starting allocation, not an empirically optimal ratio.",
    "All selected windows remain TUNING. Exposed diagnostic windows are not selected.",
    "Random draws are uniform over the declared eligible overlap-cluster representatives, "
    "not over the corpus. This selected development pool cannot estimate corpus accuracy.",
    "Style tags are coarse source/proposal descriptors, not verified catalog-style classes.",
    "Source-inventory coverage is only a tie-break proxy when target sequence coverage is equal.",
    "No-credit, long-description and title-only questions require human source inspection. "
    "Parser omissions can also be confidently wrong, so random windows retain the full checklist.",
    "The unsuccessful Phase 14 ranker's confidence is not used. No model is trained or promoted.",
    "Whole-window annotation remains subject to the two-human source-order and span audit. "
    "Planning creates no labels and demonstrates no extraction accuracy or annotation-cost gain.",
    "The reserved cohort remains sealed until all 18 phases are complete and parser is frozen.",
]


# Divide the annotation budget among difficult, underrepresented and random examples.
def quotas(budget: int, shares: tuple[int, int, int]) -> dict:
    # Mixed annotation budget must be at least three windows.
    if type(budget) is not int or budget < 3:
        raise ValueError("mixed annotation budget must be at least three windows")
    # Three positive integer annotation shares must sum to 100.
    if len(shares) != 3 or any(type(x) is not int or x <= 0 for x in shares) or sum(shares) != 100:
        raise ValueError("three positive integer annotation shares must sum to 100")
    counts = [budget * x // 100 for x in shares]
    order = sorted(range(3), key=lambda i: (-(budget * shares[i] % 100), i))
    for i in order[: budget - sum(counts)]:
        counts[i] += 1
    for i in range(3):
        if not counts[i]:
            donor = max(range(3), key=lambda j: counts[j])
            counts[donor] -= 1
            counts[i] = 1
    return dict(zip(STREAMS, counts, strict=True))


# Pick one repeatable representative for each group of overlapping page selections, while
# retaining its alternatives.
def overlap_representatives(rows: list[dict]) -> tuple[list[dict], dict]:
    """Choose stable representatives for this batch, retaining overlapping alternatives."""
    ordered = sorted(rows, key=lambda x: x["dataset_page_id"])
    parent = list(range(len(ordered)))

    # Find the shared representative for linked aliases so the same institution family stays
    # together.
    def find(i):
        # Follow alias links to the shared family representative, shortening the chain
        # as we go.
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owners = {}
    for i, row in enumerate(ordered):
        for page in row["context_physical_pages"]:
            key = (row["source_pdf_checksum"], page)
            if key in owners:
                parent[find(i)] = find(owners[key])
            else:
                owners[key] = i
    groups = {}
    for i, row in enumerate(ordered):
        groups.setdefault(find(i), []).append(row)
    clusters = {
        members[0]["dataset_page_id"]: [x["dataset_page_id"] for x in members]
        for members in groups.values()
    }
    representatives = [members[0] for members in groups.values()]
    return sorted(representatives, key=lambda x: x["dataset_page_id"]), dict(
        sorted(clusters.items())
    )


# Draw the random examples first, then fill the targeted categories and report any shortfall.
def select_batch(
    pool: list[dict], *, budget=10, seed=1501, shares=DEFAULT_SHARES, family_cap=2
) -> dict:
    """Reserve the random draw before targeted selection; never disguise shortfalls."""
    allocation = quotas(budget, shares)
    # Seed must be an integer and targeted family cap positive.
    if type(seed) is not int or type(family_cap) is not int or family_cap < 1:
        raise ValueError("seed must be an integer and targeted family cap positive")
    # Reject duplicate annotation pool window.
    if len({x["dataset_page_id"] for x in pool}) != len(pool):
        raise ValueError("duplicate annotation pool window")
    # Choose new annotations only from permitted tuning examples.
    if any(
        x["learning_partition"] != "TUNING" or x.get("evaluation_role", "TUNING") != "TUNING"
        for x in pool
    ):
        raise ValueError("annotation selection accepts TUNING only")
    eligible = [x for x in pool if x["annotation_state"] == "UNLABELLED_SOURCE_READY"]
    candidates, clusters = overlap_representatives(eligible)
    rng = random.Random(seed)
    random_rows = rng.sample(candidates, min(allocation["RANDOM"], len(candidates)))
    chosen = [{**x, "selection_stream": "RANDOM"} for x in random_rows]
    used = {x["dataset_page_id"] for x in chosen}
    targeted_families, selected_tags = Counter(), set()
    selected_styles = Counter(x["style_descriptor"] for x in random_rows)
    for stream in ("FAILURES", "STYLE_COVERAGE"):
        for _ in range(allocation[stream]):
            available = [
                x
                for x in candidates
                if x["dataset_page_id"] not in used
                and targeted_families[x["family_id"]] < family_cap
                and (stream != "FAILURES" or x["difficulty_score"] > 0)
            ]
            if not available:
                break
            if stream == "FAILURES":

                # Build the deterministic ordering key used to choose the next annotation
                # candidate.
                def key(x):
                    return (
                        targeted_families[x["family_id"]],
                        -x["difficulty_score"],
                        -len(set(x["challenge_tags"]) - selected_tags),
                        x["dataset_page_id"],
                    )
            else:

                # Build the deterministic ordering key used to choose the next annotation
                # candidate.
                def key(x):
                    return (
                        x["adjudicated_style_windows"],
                        selected_styles[x["style_descriptor"]],
                        targeted_families[x["family_id"]],
                        x["adjudicated_family_windows"],
                        x["source_inventory_style_windows"],
                        -len(set(x["challenge_tags"]) - selected_tags),
                        x["dataset_page_id"],
                    )

            row = min(available, key=key)
            chosen.append({**row, "selection_stream": stream})
            used.add(row["dataset_page_id"])
            targeted_families[row["family_id"]] += 1
            selected_styles[row["style_descriptor"]] += 1
            selected_tags.update(row["challenge_tags"])
    shortfall = {
        s: allocation[s] - sum(x["selection_stream"] == s for x in chosen) for s in STREAMS
    }
    remaining = [x for x in candidates if x["dataset_page_id"] not in used]
    backfill = rng.sample(remaining, min(budget - len(chosen), len(remaining)))
    chosen.extend({**x, "selection_stream": "FALLBACK_RANDOM"} for x in backfill)
    for i, row in enumerate(chosen, 1):
        row["review_rank"] = i
    return {
        "requested_windows": budget,
        "selected_windows": len(chosen),
        "seed": seed,
        "requested_shares": dict(zip(STREAMS, shares, strict=True)),
        "requested_quotas": allocation,
        "actual_stream_counts": dict(Counter(x["selection_stream"] for x in chosen)),
        "targeted_shortfalls_before_backfill": shortfall,
        "unfilled_windows": budget - len(chosen),
        "targeted_family_cap": family_cap,
        "random_draw_reserved_before_targeted_selection": True,
        "random_sampling_frame": [x["dataset_page_id"] for x in candidates],
        "random_slot_marginal_inclusion_probability": min(allocation["RANDOM"], len(candidates))
        / len(candidates)
        if candidates
        else None,
        "overlap_clusters": clusters,
        "eligible_windows_before_overlap_grouping": len(eligible),
        "sampling_units": len(candidates),
        "selected": chosen,
    }


# Describe a selected group of pages using source information available before annotation.
def window_features(item: dict, proposal: dict, template: dict, metrics: dict) -> dict:
    central = item["central_physical_page"]
    candidates = [x for x in proposal["candidates"] if x["physical_page"] == central]
    ids = {x["candidate_id"] for x in candidates}
    decisions = [x for x in proposal["decisions"] if x["candidate_id"] in ids]
    layouts = [x for x in proposal["layouts"] if x["physical_page"] == central]
    regions = [r for layout in layouts for r in layout["regions"] if r["role"] != "PAGE_FURNITURE"]
    blocks = [x for x in proposal["blocks"] if x["candidate_id"] in ids]
    historical = any(x["identifier_kind"].startswith("HISTORICAL") for x in candidates)
    modern = any("MODERN" in x["identifier_kind"] for x in candidates)
    columns = {r["column_id"] for r in regions if r.get("column_id") is not None}
    table = any(r["role"] == "TABLE_CELL" for r in regions)
    source_complete = bool(template["units"]) and all(
        any(u["physical_page"] == n for u in template["units"])
        for n in item["context_physical_pages"]
    )
    tags = set()
    if historical:
        tags.add("HISTORICAL_IDENTIFIER_PROPOSAL")
    if len(columns) > 1:
        tags.add("MULTICOLUMN_LAYOUT_PROPOSAL")
    if table:
        tags.add("TABLE_LAYOUT_PROPOSAL")
    if any(x["context_kind"] in {"REFERENCE_CONTAINER", "NONCOURSE_CONTAINER"} for x in decisions):
        tags.add("REFERENCE_OR_NONCOURSE_CONTEXT_PROPOSAL")
    if any(len(x["physical_pages"]) > 1 for x in blocks):
        tags.add("CROSS_PAGE_BLOCK_PROPOSAL")
    if any("OCR_UNCERTAIN" in x["identifier_kind"] for x in candidates) or any(
        "\ufffd" in u["raw_text"] for u in template["units"]
    ):
        tags.add("DAMAGED_TEXT_QUESTION")
    sequences = [x for x in proposal["field_sequence_proposals"] if x["candidate_id"] in ids]
    if sequences and any("CREDITS" not in x["selected_roles"] for x in sequences):
        tags.add("NO_CREDIT_ROLE_PROPOSED")
    reasons = {}
    # Capped categories prevent large windows from dominating by raw error counts.
    for name, present, weight in (
        (
            "KNOWN_INCOMPLETE_CORE",
            metrics.get("complete_content_eligible", 0)
            > metrics.get("complete_content_correct", 0),
            3,
        ),
        ("KNOWN_MISSED_COURSE", metrics.get("missed_courses", 0) > 0, 3),
        (
            "KNOWN_FALSE_OR_CONTAMINATED",
            metrics.get("false_course_proposals", 0) + metrics.get("description_contamination", 0)
            > 0,
            3,
        ),
        ("AMBIGUOUS_PROPOSALS", metrics.get("ambiguous_course_proposals", 0) > 0, 2),
        ("UNRESOLVED_ENTRY_PROPOSALS", any(x["status"] == "UNRESOLVED" for x in decisions), 1),
        ("SOURCE_ORDER_REVIEW_QUESTION", any(layout["warnings"] for layout in layouts), 1),
    ):
        if present:
            reasons[name] = weight
    identifier = (
        "MIXED"
        if historical and modern
        else "HISTORICAL"
        if historical
        else "MODERN"
        if modern
        else "NONE_PROPOSED"
    )
    column_style = "MULTICOLUMN" if len(columns) > 1 else "ONE_OR_UNKNOWN_COLUMN"
    style = f"{identifier}|{'TABLE' if table else 'PROSE'}|{column_style}"
    return {
        "dataset_page_id": item["dataset_page_id"],
        "institution": metrics["institution"],
        "learning_partition": item["learning_partition"],
        "learning_group": item["learning_group"],
        "source_pdf_checksum": item["source_pdf_checksum"],
        "central_physical_page": central,
        "context_physical_pages": item["context_physical_pages"],
        "style_descriptor": style,
        "challenge_tags": sorted(tags),
        "difficulty_score": sum(reasons.values()),
        "difficulty_reason_weights": reasons,
        "difficulty_score_is_probability": False,
        "inventory_review_status": metrics["status"],
        "source_units": len(template["units"]),
        "longest_proposed_block_words": max(
            (len(re.findall(r"\S+", b["raw_text"])) for b in blocks), default=0
        ),
        "annotation_state": "UNLABELLED_SOURCE_READY"
        if source_complete
        else "SOURCE_PREPARATION_REQUIRED",
        "source_order_sha256": source_order_digest(template),
        "labels_supplied": False,
    }


# Choose the next annotation batch, write blank review packets and record exactly how each
# page was selected.
def prepare_annotation_batch(
    pilot: Path,
    evaluation: Path,
    templates: Path,
    output: Path,
    *,
    reviewed: Path | None = None,
    budget=10,
    seed=1501,
    shares=DEFAULT_SHARES,
    family_cap=2,
) -> dict:
    _reject_reserved_paths(pilot, evaluation, templates, output, *([reviewed] if reviewed else []))
    quotas(budget, shares)
    metadata = load_exposed_split_metadata(pilot, evaluation, output)
    audit = audit_split_metadata(metadata)
    manifest = read_json(_metadata_file(evaluation, "evaluation_manifest.json"))
    # Reject a selection whose evaluation already opened protected material.
    if manifest["protected_evaluation_opened"] is not False:
        raise ValueError("annotation selection evaluation opened protected material")
    predictions = Path(manifest["prediction_path"])
    _reject_reserved_paths(predictions)
    pm_path = _metadata_file(predictions, "prediction_manifest.json")
    # Stop if the source-only prediction manifest changed.
    if sha256(pm_path) != manifest["prediction_manifest_sha256"]:
        raise ValueError("source-only prediction manifest changed")
    pm = read_json(pm_path)
    tm = read_json(_metadata_file(templates, "manifest.json"))
    # The blank templates and predictions must describe the same verified source-only sample.
    if (
        pm["source_truth_read"] is not False
        or pm["protected_evaluation_opened"] is not False
        or pm["pilot_manifest_sha256"] != manifest["pilot_manifest_sha256"]
        or tm["pilot_manifest_sha256"] != manifest["pilot_manifest_sha256"]
        or tm["prediction_manifest_sha256"] != sha256(pm_path)
        or tm["protected_evaluation_opened"] is not False
        or tm["source_truth_read"] is not False
    ):
        raise ValueError("annotation templates/predictions differ from bound source-only pilot")

    # Check the saved file fingerprint before reading this cached input.
    def pinned(root, name, hashes):
        path = _metadata_file(root, name)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(path) != hashes.get(name):
            raise ValueError("annotation input checksum changed")
        return read_jsonl(path)

    template_rows = pinned(templates, "sequence_template.jsonl", tm["file_sha256"])
    tuning_ids = {x["dataset_page_id"] for x in metadata if x["learning_partition"] == "TUNING"}
    # Source templates must cover exactly the bound TUNING inventory.
    if (
        len(template_rows) != len(tuning_ids)
        or {x["dataset_page_id"] for x in template_rows} != tuning_ids
    ):
        raise ValueError("source templates must cover exactly the bound TUNING inventory")
    for row in template_rows:
        source = next(x for x in metadata if x["dataset_page_id"] == row["dataset_page_id"])
        # Stop if the annotation template source identity differs from metadata.
        if any(
            row[k] != source[k]
            for k in (
                "learning_partition",
                "learning_group",
                "source_pdf_checksum",
                "central_physical_page",
                "context_physical_pages",
            )
        ):
            raise ValueError("annotation template source identity differs from metadata")
        # Frozen source templates must contain blank pending labels.
        if row["review_status"] != "PENDING" or any(
            u[k] is not None
            for u in row["units"]
            for k in ("label", "owner_start_node_id", "boundary_after", "transition_cue")
        ):
            raise ValueError("frozen source templates must contain blank pending labels")
    readiness = audit_sequence_reviews(
        templates, reviewed or _metadata_file(templates, "sequence_template.jsonl")
    )
    page_rows = pinned(evaluation, "page_metrics.jsonl", manifest["file_sha256"])
    proposal_rows = pinned(predictions, "page_proposals.jsonl", pm["file_sha256"])
    for rows in (page_rows, proposal_rows):
        # Stop if the annotation evidence inventory differs from exposed metadata.
        if len(rows) != len(metadata) or {x["dataset_page_id"] for x in rows} != {
            x["dataset_page_id"] for x in metadata
        }:
            raise ValueError("annotation evidence inventory differs from exposed metadata")
    pages = {x["dataset_page_id"]: x for x in page_rows}
    proposals = {x["dataset_page_id"]: x for x in proposal_rows}
    sources = {x["dataset_page_id"]: x for x in template_rows}
    pool = []
    reviewed_states = {x["dataset_page_id"]: x for x in readiness["window_readiness"]}
    for item in metadata:
        if item["learning_partition"] != "TUNING":
            continue
        page = pages[item["dataset_page_id"]]
        # Stop if the annotation metric exposure/identity changed.
        if (
            any(page[k] != item[k] for k in ("learning_partition", "learning_group"))
            or page["physical_page"] != item["central_physical_page"]
        ):
            raise ValueError("annotation metric exposure/identity changed")
        proposal = proposals[item["dataset_page_id"]]
        # Annotation selection must not use source-review answers to generate its proposals.
        if proposal.get("source_truth_read") is not False:
            raise ValueError("annotation proposals read source truth")
        row = window_features(item, proposal, sources[item["dataset_page_id"]], page)
        state = reviewed_states.get(item["dataset_page_id"], {})
        if state.get("training_eligible"):
            row["annotation_state"] = "ADJUDICATED_TARGETS_AVAILABLE"
        elif state.get("reason") == "SOURCE_INSUFFICIENT":
            row["annotation_state"] = "SOURCE_PREPARATION_REQUIRED"
        row["family_id"] = audit["family_membership"][item["dataset_page_id"]]
        pool.append(row)
    # Source-derived quartile is a review question, never a description label.
    sizes = sorted(
        x["longest_proposed_block_words"] for x in pool if x["longest_proposed_block_words"]
    )
    long_threshold = sizes[(3 * (len(sizes) - 1)) // 4] if sizes else None
    for row in pool:
        if long_threshold is not None and row["longest_proposed_block_words"] >= long_threshold:
            row["challenge_tags"] = sorted(
                set(row["challenge_tags"]) | {"LONG_BLOCK_PROPOSAL_QUESTION"}
            )
    target_styles = Counter(
        x["style_descriptor"]
        for x in pool
        if x["annotation_state"] == "ADJUDICATED_TARGETS_AVAILABLE"
    )
    target_families = Counter(
        x["family_id"] for x in pool if x["annotation_state"] == "ADJUDICATED_TARGETS_AVAILABLE"
    )
    inventory_styles = Counter(
        x["style_descriptor"] for x in pool if x["inventory_review_status"] == "SCORED_DEVELOPMENT"
    )
    for row in pool:
        row["adjudicated_style_windows"] = target_styles[row["style_descriptor"]]
        row["adjudicated_family_windows"] = target_families[row["family_id"]]
        row["source_inventory_style_windows"] = inventory_styles[row["style_descriptor"]]
    selection = select_batch(pool, budget=budget, seed=seed, shares=shares, family_cap=family_cap)
    result = {
        "schema_version": VERSION,
        "status": "MIXED_TUNING_ANNOTATION_BATCH_READY",
        "selection": selection,
        "annotation_readiness": readiness,
        "pool_state_counts": dict(Counter(x["annotation_state"] for x in pool)),
        "target_annotation_style_coverage": dict(target_styles),
        "source_inventory_style_coverage_proxy": dict(inventory_styles),
        "long_block_question_threshold_words": long_threshold,
        "manual_review_checklist": CHECKLIST,
        "limits": LIMITS,
        "protected_evaluation_opened": False,
        "parser_or_training_targets_modified": False,
        "corpus_accuracy_estimation_permitted": False,
        "accuracy_gain_claimed": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "annotation_pool.jsonl", pool)
    selected_sources = [sources[x["dataset_page_id"]] for x in selection["selected"]]
    write_jsonl(output / "batch_template.jsonl", selected_sources)
    write_json(output / "annotation_plan.json", result)
    _viewer(output, result, pilot, manifest["pilot_manifest_sha256"])
    write_json(
        output / "manifest.json",
        {
            "schema_version": VERSION,
            "protected_evaluation_opened": False,
            "input_sha256": {
                "pilot_manifest": sha256(_metadata_file(pilot, "pilot_manifest.json")),
                "metadata": sha256(_metadata_file(pilot, "selected_pages.jsonl")),
                "evaluation_manifest": sha256(
                    _metadata_file(evaluation, "evaluation_manifest.json")
                ),
                "cached_page_metrics": sha256(_metadata_file(evaluation, "page_metrics.jsonl")),
                "prediction_manifest": sha256(pm_path),
                "source_proposals": sha256(_metadata_file(predictions, "page_proposals.jsonl")),
                "template_manifest": sha256(_metadata_file(templates, "manifest.json")),
                "templates": sha256(_metadata_file(templates, "sequence_template.jsonl")),
                "audited_review": readiness["review_sha256"],
            },
            "code_sha256": {
                name: sha256(Path(__file__).with_name(name))
                for name in (
                    "active_annotations.py",
                    "sequence_annotations.py",
                    "evaluation_design.py",
                )
            },
            "file_sha256": artifact_hashes(output),
        },
    )
    return {
        "status": result["status"],
        "selected_windows": selection["selected_windows"],
        "streams": selection["actual_stream_counts"],
        "pool_states": result["pool_state_counts"],
        "target_labels_supplied": False,
        "protected_evaluation_opened": False,
    }


# Copy only selected, hash-bound TUNING packet images for concrete source review.
def _viewer(output: Path, result: dict, pilot: Path, expected_pilot_hash: str) -> None:
    """Copy only selected, hash-bound TUNING packet images for concrete source review."""

    # Escape displayed text so it cannot be interpreted as HTML markup.
    def esc(x):
        return html.escape(str(x))

    rows = []
    selection = result["selection"]
    pilot_manifest = _metadata_file(pilot, "pilot_manifest.json")
    # Stop if the annotation pilot changed before source-image preparation.
    if sha256(pilot_manifest) != expected_pilot_hash:
        raise ValueError("annotation pilot changed before source-image preparation")
    pm = read_json(pilot_manifest)
    selected = _metadata_file(pilot, "selected_pages.jsonl")
    # Stop if the annotation metadata changed before source-image preparation.
    if sha256(selected) != pm["file_sha256"].get("selected_pages.jsonl"):
        raise ValueError("annotation metadata changed before source-image preparation")
    metadata = {x["dataset_page_id"]: x for x in read_jsonl(selected)}
    for row in selection["selected"]:
        page_id = row["dataset_page_id"]
        # Reject unsafe annotation window identity.
        if not re.fullmatch(r"[A-Za-z0-9_:-]+", page_id):
            raise ValueError("unsafe annotation window identity")
        item = metadata[page_id]
        name = item["packet_path"] + "/source_reference.json"
        reference_path = _metadata_file(pilot, name)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if sha256(reference_path) != pm["file_sha256"].get(name):
            raise ValueError("source packet reference checksum changed")
        reference = read_json(reference_path)
        # Stop if the selected annotation image identity changed.
        if reference["selected_page"] != item or item["learning_partition"] != "TUNING":
            raise ValueError("selected annotation image identity changed")
        destination = output / "windows" / page_id
        destination.mkdir(parents=True)
        images = []
        # Stop if the annotation images do not cover frozen context.
        if {x["physical_page"] for x in reference["page_artifacts"]} != set(
            item["context_physical_pages"]
        ):
            raise ValueError("annotation images do not cover frozen context")
        for asset in reference["page_artifacts"]:
            image = Path(asset["image_path"])
            _reject_reserved_paths(image)
            try:
                relative = str(image.resolve().relative_to(pilot.resolve()))
            # Report value error as an explicit failure: annotation image escapes bound pilot.
            except ValueError as error:
                raise ValueError("annotation image escapes bound pilot") from error
            image = _metadata_file(pilot, relative)
            # Reject this input if its file fingerprint no longer matches the recorded
            # version.
            if sha256(image) != asset["image_sha256"] or asset["image_sha256"] != pm[
                "file_sha256"
            ].get(relative):
                raise ValueError("annotation source image checksum changed")
            image_name = f"page_{asset['physical_page']:05d}.png"
            shutil.copyfile(image, destination / image_name)
            images.append(
                f'<h2>Physical page {asset["physical_page"]}</h2><img src="{image_name}" '
                'style="max-width:100%" alt="Frozen source page">'
            )
        (destination / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            f"<title>Annotation source — {esc(page_id)}</title><h1>{esc(row['institution'])}</h1>"
            "<p>Check images, source order and every character. Labels remain blank. "
            "Two distinct human reviewers are required before targets become training eligible.</p>"
            + "".join(images)
            + "</html>",
            encoding="utf-8",
        )
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{esc(v)}</td>"
                for v in (
                    row["review_rank"],
                    row["selection_stream"],
                    row["institution"],
                    row["central_physical_page"],
                    row["source_units"],
                    row["style_descriptor"],
                    ", ".join(row["difficulty_reason_weights"]) or "Random/style coverage",
                )
            )
            + f'<td><a href="windows/{page_id}/index.html">Inspect source</a></td></tr>'
        )
    checklist = "".join(f"<li>{esc(x)}</li>" for x in CHECKLIST)
    limits = "".join(f"<li>{esc(x)}</li>" for x in LIMITS)
    text = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase 15 — Mixed annotation selection</title><style>
body{{font:16px/1.6 system-ui;max-width:1250px;margin:35px auto;padding:0 24px;color:#233044}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;text-align:left;
border-bottom:1px solid #ddd}}th{{background:#e6eff5}}.scroll{{overflow:auto}}
.note{{padding:18px;background:#edf3f8;border-left:5px solid #39748f}}a{{color:#185c98}}</style>
<h1>Phase 15 — Mix difficult, undercovered and random windows</h1>
<p class="note">{selection["selected_windows"]} whole TUNING windows selected for annotation.
No labels were manufactured, no model was trained and no parser accuracy gain is claimed.
The reserved cohort remains sealed.</p>
<p>The 40/30/30 suggestion is a useful initial policy, not a validated optimum.
The random draw is reserved first, before failure or style selection.
Targeted selection is capped at {selection["targeted_family_cap"]} windows per institution family;
the random draw is unconstrained by scores or family quotas.</p>
<p>Requested: {esc(selection["requested_quotas"])}.
Actual: {esc(selection["actual_stream_counts"])}.
Seed: {selection["seed"]}; eligible overlap-grouped sampling units: {selection["sampling_units"]}.
Source preparation / completed annotation exclusions: {esc(result["pool_state_counts"])}.</p>
<div class="scroll"><table><tr><th>Rank</th><th>Stream</th><th>Institution</th><th>Page</th>
<th>Units</th><th>Coarse style descriptor</th><th>Selection evidence</th><th>Source</th></tr>
{"".join(rows)}</table></div>
<h2>Annotation checklist</h2><ul>{checklist}</ul>
<p>The batch preserves the exact frozen source template and its blank field, owner and boundary
slots. Review whole central/context windows, including negative text. Pending suggestions never
count as gold. Use the existing sequence annotation audit after two human source reviews.</p>
<h2>Interpretation and next cycle</h2><ul>{limits}</ul>
<p>Rerun with audited reviews to exclude completed target annotations and update actual target
style coverage. Source-inventory coverage is shown separately. Shortfalls and random backfill
remain explicit. Preserve an independent evaluation set;
this acquisition batch is for development.</p>
<p><a href="annotation_plan.json">Selection policy and full plan</a> ·
<a href="annotation_pool.jsonl">Pool, source readiness and coverage</a> ·
<a href="batch_template.jsonl">Blank source annotation batch</a> ·
<a href="manifest.json">Input/code/image provenance</a> ·
<a href="https://burrsettles.com/pub/settles.activelearning_20090109.pdf">Active learning survey</a>
</p></html>"""
    (output / "index.html").write_text(text, encoding="utf-8")
