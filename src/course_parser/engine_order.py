# Use an external layout tool as a second opinion on reading order. Match its proposed order
# to the original PDF text pieces without replacing or dropping them.
"""Bind external layout order to verified native source cells, without model text."""

from __future__ import annotations

from collections import Counter

from .docling_adapter import parse_docling_page
from .io import contained_file, read_json, sha256
from .layout_models import Box, intersection_fraction
from .learning_layout import baseline_rows
from .ppstructure_adapter import parse_ppstructure_page


# Bind the external ordering export to the same verified native original text pieces.
def external_order(proposal, selected, packet, exports, engine, layout_module):
    manifest = read_json(exports / "manifest.json")
    # Stop if the engine manifest is not a source-only run of the requested engine.
    if manifest.get("source_truth_read") is not False or manifest.get("engine") != engine:
        raise ValueError("engine manifest is not a source-only run of the requested engine")
    key = f"{selected['dataset_page_id']}:p{proposal.physical_page}"
    entry = manifest["pages"][key]
    source = read_json(packet / "source_reference.json")
    asset = next(
        a for a in source["page_artifacts"] if a["physical_page"] == proposal.physical_page
    )
    for name, expected in (
        ("source_pdf_checksum", selected["source_pdf_checksum"]),
        ("image_sha256", asset["image_sha256"]),
        ("embedded_geometry_sha256", asset["embedded_geometry_sha256"]),
    ):
        # Stop if the engine export differs from verified source.
        if entry[name] != expected:
            raise ValueError(f"engine export differs from verified source: {name}")
    path = contained_file(exports, entry["export_path"])
    # Reject this input if its file fingerprint no longer matches the recorded version.
    if sha256(path) != entry["export_sha256"]:
        raise ValueError("engine export checksum changed")
    if not proposal.regions or proposal.page_width is None or proposal.page_height is None:
        raw = proposal.model_dump(mode="json")
        raw["warnings"].append("EXTERNAL_ORDER_HAS_NO_NATIVE_SOURCE_GEOMETRY")
        return layout_module.PageLayoutProposal.model_validate(raw)
    if engine == "ppstructure":
        prediction = parse_ppstructure_page(
            read_json(path),
            page_key=key,
            image_width=entry["image_width"],
            image_height=entry["image_height"],
            width=proposal.page_width,
            height=proposal.page_height,
            engine_version=manifest["engine_version"],
        ).layout
    else:
        prediction = parse_docling_page(
            read_json(path),
            page_no=entry["export_page_no"],
            page_key=key,
            width=proposal.page_width,
            height=proposal.page_height,
            engine_version=manifest["engine_version"],
        )
    return project_region_order(proposal, prediction, layout_module)


# Keep all cells. Ambiguous/uncovered membership cannot assert safe order.
def project_region_order(proposal, prediction, layout_module):
    """Keep all cells. Ambiguous/uncovered membership cannot assert safe order."""
    raw = proposal.model_dump(mode="json")
    assignments, uncertain = {}, []
    # A native source heading can disagree with an engine's furniture label.
    # Keep the native role and place that model header/footer by page position.
    furniture = [r for r in prediction.regions if r.furniture]
    before = sorted(
        (r for r in furniture if (r.box.y0 + r.box.y1) / 2 < proposal.page_height / 2),
        key=lambda r: (r.box.y0, r.box.x0),
    )
    after = sorted((r for r in furniture if r not in before), key=lambda r: (r.box.y0, r.box.x0))
    reading_order = list(
        dict.fromkeys(
            [
                *(r.region_id for r in before),
                *prediction.reading_order,
                *(r.region_id for r in after),
            ]
        )
    )
    region_rank = {rid: rank for rank, rid in enumerate(reading_order)}
    for cell in raw["regions"]:
        if cell["role"] == "PAGE_FURNITURE":
            continue
        g = cell["geometry"]
        b = Box(x0=g["x0"], y0=g["y0"], x1=g["x1"], y1=g["y1"])
        scores = sorted(
            (intersection_fraction(b, r.box), r.region_id)
            for r in prediction.regions
            if r.region_id in region_rank
        )
        scores.reverse()
        if (
            not scores
            or scores[0][0] < 0.5
            or (len(scores) > 1 and scores[1][0] >= 0.5 and scores[0][0] - scores[1][0] < 0.1)
        ):
            uncertain.append(cell)
        else:
            assignments[cell["region_id"]] = scores[0][1]
    grouped = {}
    for cell in raw["regions"]:
        if cell["region_id"] in assignments:
            grouped.setdefault(assignments[cell["region_id"]], []).append(cell)
    ordered = [cell for rid in reading_order for cell in baseline_rows(grouped.get(rid, []))]
    # Retain uncertain cells as explicit unresolved islands, not silent fallback.
    ordered.extend(sorted(uncertain, key=lambda r: r["source_order"]))
    counts = Counter(r["region_id"] for r in ordered)
    expected = {r["region_id"] for r in raw["regions"] if r["role"] != "PAGE_FURNITURE"}
    # Project external regions onto native text without losing or duplicating text pieces.
    if set(counts) != expected or any(n != 1 for n in counts.values()):
        raise ValueError("external region projection lost or duplicated native cells")
    uncertain_ids = {r["region_id"] for r in uncertain}
    raw["reading_order"] = [
        dict(
            source_region_id=a["region_id"],
            target_region_id=b["region_id"],
            status="UNRESOLVED"
            if a["region_id"] in uncertain_ids or b["region_id"] in uncertain_ids
            else "CONFIRMED",
            reason="EXTERNAL_REGION_ORDER_WITH_NATIVE_SOURCE_ROWS",
            confidence=None,
        )
        for a, b in zip(ordered, ordered[1:], strict=False)
    ]
    # Column identities stay from the native gutter detector; region != column.
    # Engine labels may help inspect ownership but never overwrite printed text.
    raw["engine_version"] += f"+{prediction.engine.lower()}-{prediction.engine_version}"
    raw["engine"] = prediction.engine
    if uncertain or prediction.reading_order_status != "MODEL_PROPOSED":
        raw["status"] = "PARTIAL"
    disagreements = sum(len(grouped.get(r.region_id, [])) for r in furniture)
    raw["warnings"].extend(
        [
            f"EXTERNAL_ORDER_UNASSIGNED_NATIVE_CELLS:{len(uncertain)}",
            *prediction.warnings,
            "EXTERNAL_REGION_ORDER_WITH_COMMON_NATIVE_TEXT_ROLES_AND_ROWS",
            f"EXTERNAL_FURNITURE_NATIVE_BODY_LABEL_DISAGREEMENTS:{disagreements}",
        ]
    )
    return layout_module.PageLayoutProposal.model_validate(raw)
