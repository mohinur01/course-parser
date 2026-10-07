#!/usr/bin/env python3
# Purpose: Check stored Docling export compatibility without claiming source-grounded accuracy.
"""Check stored Docling export compatibility without claiming source-grounded accuracy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from course_parser.docling_adapter import parse_docling_page  # noqa: E402
from course_parser.io import read_json, sha256, write_json  # noqa: E402


# Read the command-line options, then check that Docling exports contain the expected pages
# and layout information before those exports are imported into the project.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exports-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for path in sorted(args.exports_dir.glob("*_docling.json")):
        raw = read_json(path)
        for number, page in raw["pages"].items():
            entry = {
                "path": str(path.resolve()),
                "sha256": sha256(path),
                "docling_page_no": int(number),
                "document_schema_version": raw.get("version"),
            }
            try:
                size = page["size"]
                prediction = parse_docling_page(
                    raw,
                    page_no=int(number),
                    page_key=f"compatibility:{path.stem}:p{number}",
                    width=size["width"],
                    height=size["height"],
                    engine_version="not-recorded-in-export",
                )
                entry.update(
                    status="PARSED",
                    regions=len(prediction.regions),
                    tables=len(prediction.tables),
                    reading_order_status=prediction.reading_order_status,
                    warnings=prediction.warnings,
                )
            except (ValueError, KeyError, TypeError, RecursionError) as exc:
                entry.update(status="REJECTED", error=str(exc))
            results.append(entry)
    # No stored Docling exports found.
    if not results:
        raise ValueError("no stored Docling exports found")
    report = {
        "status": "PASS" if all(row["status"] == "PARSED" for row in results) else "INCOMPLETE",
        "pages": len(results),
        "regions": sum(row.get("regions", 0) for row in results),
        "tables": sum(row.get("tables", 0) for row in results),
        "scope": "DOCLING_EXPORT_SCHEMA_COMPATIBILITY_ONLY",
        "docling_inference_run": False,
        "source_binding_verified": False,
        "course_accuracy_measured": False,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
