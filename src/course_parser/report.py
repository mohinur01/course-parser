# Create a local report for inspecting source evidence and pending decisions. Escape displayed
# text so PDF content is shown as text rather than interpreted as HTML.
"""Static, escaped source viewer; there are no controls that submit decisions."""

from __future__ import annotations

import html
from pathlib import Path


# Escape displayed text so it cannot be interpreted as HTML markup.
def esc(value) -> str:
    return html.escape(str(value), quote=True)


# Convert the evidence status into a readable report label.
def status_label(record: dict) -> str:
    return (
        "Source evidence ready"
        if record["evidence_status"] == "SOURCE_EVIDENCE_READY"
        else "Source evidence incomplete"
    )


# Write the source-evidence report while keeping unreviewed decisions visibly pending.
def write_report(output: Path, tasks: list[dict], pages: list[dict], hits: list[dict]) -> None:
    page_map = {page["page_key"]: page for page in pages}
    rows = []
    panels = []
    for task in tasks:
        identifier = task["page_task_id"]
        html_id = f"task-{task['review_rank']}"
        rows.append(
            f'<a class="task-card" href="#{html_id}">'
            f"<strong>{esc(task['institution_reported'])}</strong>"
            f"<span>Rank {task['review_rank']} · central page {task['central_physical_page']}"
            f" · {task['hit_count']} hits</span>"
            f"<span>Reported year: {esc(task['catalog_year_reported'])}</span>"
            f"<span>{status_label(task)}</span><code>{esc(identifier)}</code></a>"
        )
        body = [
            f'<section id="{html_id}"><h2>{esc(task["institution_reported"])} · '
            f"central page {task['central_physical_page']}</h2>"
            f"<p>{esc(identifier)} · {status_label(task)} · "
            "Pending source review</p>"
        ]
        if task["errors"]:
            body.append(f'<p class="error">{esc("; ".join(task["errors"]))}</p>')
        body.append('<div class="pages">')
        # The central page is first even when the viewer stacks on a narrow screen.
        ordered_keys = sorted(
            task["page_keys"],
            key=lambda key: (
                page_map[key]["physical_page"] != task["central_physical_page"],
                page_map[key]["physical_page"],
            ),
        )
        for key in ordered_keys:
            page = page_map[key]
            role = (
                "CENTRAL" if page["physical_page"] == task["central_physical_page"] else "CONTEXT"
            )
            body.append(f"<article><h3>{role} · physical page {page['physical_page']}</h3>")
            if page["image_relative_path"]:
                body.append(
                    f'<a href="{esc(page["image_relative_path"])}">'
                    f'<img src="{esc(page["image_relative_path"])}" '
                    f'alt="Verified PDF render, physical page {page["physical_page"]}">'
                    "Open image at full size</a>"
                )
            if page["v3_page_source"]:
                raw = page["v3_page_source"]["representations"][0]["raw_text"]
                body.append(
                    f"<details><summary>Frozen source text</summary><pre>{esc(raw)}</pre></details>"
                )
            if page["errors"]:
                body.append(f'<p class="error">{esc("; ".join(page["errors"]))}</p>')
            body.append("</article>")
        body.append(
            '</div><div class="table-scroll"><table><tr><th>Original hit</th><th>Concept</th>'
            "<th>Exact frozen source quote</th><th>Evidence</th><th>Decision</th></tr>"
        )
        for hit in hits:
            if hit["page_task_id"] == identifier:
                quote = hit["source_quote"] if hit["source_quote"] is not None else "Unavailable"
                body.append(
                    f"<tr><td>{esc(hit['hit_id'])}</td><td>{esc(hit['canonical_concept'])}</td>"
                    f"<td>{esc(quote)}</td><td>{status_label(hit)}</td>"
                    "<td>Pending human source review</td></tr>"
                )
        body.append("</table></div></section>")
        panels.append("".join(body))
    content = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Course parser — source evidence</title><style>
body{font:15px system-ui,sans-serif;line-height:1.5;background:#f5f6f8;color:#20262e;margin:0}
main{max-width:1440px;margin:auto;padding:20px}h1{font-size:25px}h2{font-size:20px}h3{font-size:16px}
section{background:white;padding:20px;margin-top:24px;
border:1px solid #ccd2d9;scroll-margin-top:16px}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:700px;
margin:16px 0}th,td{border-bottom:1px solid #d7dde4;
padding:9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}a{color:#175aad}
.task-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:14px}
.task-card{display:grid;gap:5px;background:white;border:1px solid #ccd2d9;padding:16px;
text-decoration:none;border-radius:6px}.task-card:hover{border-color:#175aad}
.pages{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:18px}
article{min-width:0}img{display:block;width:100%;border:1px solid #d7dde4}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto}
.error{color:#9a3025}details{margin-top:12px}code{overflow-wrap:anywhere}
@media(max-width:500px){main{padding:14px}section{padding:12px}h1{font-size:23px}}
</style></head><body><main><h1>Course parser — source evidence</h1>
<p>Inspect the central page first. Adjacent pages provide boundary and continuation context.
Every hit remains pending source review; this batch cannot establish a catalog-level negative.</p>
<p><a href="coverage.json">Coverage</a> ·
<a href="hit_disposition_template.csv">Blank dispositions</a>
 · <a href="run_manifest.json">Run provenance</a></p>
<nav class="task-list" aria-label="Page tasks">"""
    content += "".join(rows) + "</nav>" + "".join(panels) + "</main></body></html>"
    with (output / "index.html").open("x", encoding="utf-8") as stream:
        stream.write(content)
