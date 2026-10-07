# Build a local HTML viewer that shows page images, text boxes and keyword-hit locations. This
# lets reviewers inspect the source behind a layout proposal.
"""Display page structure and exact hit geometry without assigning ownership."""

from __future__ import annotations

from pathlib import Path

from .report import esc


# Draw a labeled source box over the page image using the supplied coordinates and color.
def _rectangle(box, color, label):
    return (
        f'<rect x="{box["x0"]}" y="{box["y0"]}" '
        f'width="{box["x1"] - box["x0"]}" height="{box["y1"] - box["y0"]}" '
        f'fill="none" stroke="{color}" stroke-width="1.3"><title>{esc(label)}</title></rect>'
    )


# Write a page viewer with source images and overlays for text, layout regions and keyword
# hits.
def write_layout_viewer(output: Path, pages: list[dict], hits: list[dict]):
    sections = []
    for page in pages:
        predictions = page["predictions"]
        heading = f"Physical page {page['physical_page']}"
        body = [f"<section><h2>{heading}</h2><p><code>{esc(page['page_key'])}</code></p>"]
        if page["image_relative_path"]:
            if page["width"] and page["height"]:
                body.append(
                    f'<svg viewBox="0 0 {page["width"]} {page["height"]}" '
                    f'role="img" aria-label="Page {page["physical_page"]} with layout overlays">'
                    f'<image href="{esc(page["image_relative_path"])}" width="{page["width"]}" '
                    f'height="{page["height"]}"/>'
                )
                if predictions:
                    for region in predictions["regions"]:
                        body.append(_rectangle(region["box"], "#2362cc", region["label"]))
                if page.get("ppstructure"):
                    for region in page["ppstructure"]["layout"]["regions"]:
                        body.append(_rectangle(region["box"], "#00805d", "PP: " + region["label"]))
                for hit in hits:
                    if hit["geometry_status"] != "EXACT":
                        continue
                    for projection in hit["geometry"]:
                        if projection["page_key"] == page["page_key"]:
                            for fragment in projection["fragments"]:
                                body.append(_rectangle(fragment["box"], "#c85b00", hit["hit_id"]))
                body.append("</svg>")
            else:
                body.append(f'<img src="{esc(page["image_relative_path"])}" alt="{heading}">')
            body.append(
                f'<p><a href="{esc(page["image_relative_path"])}">'
                "Open source image at full size</a></p>"
            )
        body.append(
            f"<p>Native text cells: {len(page['native_cells'])}. "
            f"Frozen/native alignment: {esc(page['frozen_native_alignment'].lower())}.</p>"
        )
        if predictions:
            body.append(
                "<p>Reading order: "
                f"{esc(predictions['reading_order_status'].lower().replace('_', ' '))}. "
                f"Layout source: {esc(predictions['engine'])} "
                f"({esc(predictions['engine_version'])}).</p>"
            )
            counts = {
                name: sum(value["status"] == name for value in predictions["assignments"])
                for name in ("ASSIGNED", "AMBIGUOUS", "ORPHAN")
            }
            body.append(
                f"<p>Text cells assigned: {counts['ASSIGNED']}; ambiguous: "
                f"{counts['AMBIGUOUS']}; unassigned: {counts['ORPHAN']}.</p>"
            )
            for table in predictions["tables"]:
                body.append(
                    f"<p>Table proposal: {table['rows']} rows × {table['columns']} columns, "
                    f"{len(table['cells'])} logical cells.</p>"
                )
            for warning in predictions["warnings"]:
                body.append(f"<p>{esc(warning)}</p>")
        if page.get("ppstructure"):
            pp = page["ppstructure"]
            body.append(
                f"<p>PP-Structure reading order: {esc(pp['layout']['reading_order_status'])}. "
                f"Engine version: {esc(pp['layout']['engine_version'])}. "
                "Green rectangles are its independent region proposals.</p>"
            )
            if pp["ocr"]:
                ocr = pp["ocr"]
                body.append(
                    f"<p>PP OCR: {len(ocr['lines'])} recognized lines; "
                    f"{len(ocr['detections'])} detection boxes. "
                    f"Review route: {esc(page['ppstructure_routing'])}.</p>"
                )
                body.append("<details><summary>PP OCR text and recognition scores</summary>")
                for line in ocr["lines"]:
                    body.append(
                        f"<p>{esc(line['text'])} "
                        f"<small>(recognition score {line['recognition_score']:.3f})</small></p>"
                    )
                for flag in ocr["review_flags"]:
                    body.append(f"<p>{esc(flag)}</p>")
                body.append("</details>")
            comparison = page["layout_comparison"]
            body.append(f"<p>Docling / PP-Structure comparison: {esc(comparison['status'])}.</p>")
            for flag in comparison["review_flags"]:
                body.append(f"<p>{esc(flag)}</p>")
            for route in pp["specialized_routes"]:
                body.append(f"<p>{esc(route['label'])}: {esc(route['route'])}.</p>")
            for warning in pp["layout"]["warnings"]:
                body.append(f"<p>{esc(warning)}</p>")
            body.append(
                f'<p><a href="{esc(page["ppstructure_export_relative_path"])}">'
                "Raw PP-Structure export</a></p>"
            )
        for error in page["errors"]:
            body.append(f'<p class="error">{esc(error)}</p>')
        if page["frozen_representation"]:
            body.append(
                "<details><summary>Original frozen text</summary>"
                f"<pre>{esc(page['frozen_representation']['raw_text'])}</pre></details>"
            )
        if page["backend_representation"]:
            body.append(
                "<details><summary>PDF backend text</summary>"
                f"<pre>{esc(page['backend_representation']['raw_text'])}</pre></details>"
            )
        body.append("</section>")
        sections.append("".join(body))
    content = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Course parser — page structure</title><style>
body{font:16px system-ui,sans-serif;background:#f5f6f8;color:#20262e;margin:0;line-height:1.5}
main{max-width:1100px;margin:auto;padding:20px}section{background:white;padding:20px;
border:1px solid #ccd2d9;margin-top:20px}svg,img{width:100%;max-height:950px;display:block}
code,pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#175aad}.error{color:#9a3025}
.blue{color:#2362cc}.orange{color:#a64600}h1{font-size:26px}h2{font-size:21px}
</style></head><body><main><h1>Course parser — page structure</h1>
<p><span class="blue">Blue: layout regions.</span>
<span style="color:#00805d">Green: optional PP-Structure regions.</span>
<span class="orange">Orange: exact original keyword spans.</span>
All keyword decisions remain pending source review.</p>
<p><a href="layout_qc.json">Quality checks</a> ·
<a href="page_layout.jsonl">Page structure records</a> ·
<a href="hit_geometry.jsonl">Original hit geometry</a></p>"""
    if any(
        page.get("ppstructure")
        and page["ppstructure"]["layout"]["engine_version"].startswith("synthetic")
        for page in pages
    ):
        content += (
            "<p><strong>Synthetic model contract fixtures. "
            "No neural inference was run to create this example.</strong></p>"
        )
    (output / "index.html").write_text(
        content + "".join(sections) + "</main></body></html>", encoding="utf-8"
    )
