# Build small, clearly synthetic PDFs, text streams and review packets used by the tests.
"""Deliberately synthetic PDFs; these never enter the user's frozen review queue."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import fitz

from course_parser.io import sha256, write_csv, write_json


# Bundle the temporary source files and settings shared by the tests.
@dataclass
class Fixture:
    root: Path
    release: Path
    vocabulary: Path
    pdf: Path
    stream: Path
    tasks: list[dict]
    hits: list[dict]

    # Refresh the synthetic release checksums after a deliberate fixture change.
    def refresh_release(self):
        path = self.release / "task_release_manifest.json"
        value = json.loads(path.read_text())
        value["files_sha256"] = {
            name: sha256(self.release / name) for name in value["files_sha256"]
        }
        path.write_text(json.dumps(value, indent=2) + "\n")


# Create synthetic catalog pages, extracted text, keyword hits and a checksummed test release.
def create_fixture(root: Path, reference, *, corrupt_lexical_quote: bool = False) -> Fixture:
    root.mkdir(parents=True, exist_ok=False)
    release = root / "release"
    release.mkdir()
    pdf_path = root / "synthetic-catalog.pdf"
    body = (
        "Study of ore-forming processes and mineral systems."
        if not corrupt_lexical_quote
        else "Study of ore dep0sits and mineral systems."
    )
    printed = [
        "SYNTHETIC UNIVERSITY - TEST FIXTURE\nDepartment of Geology\nCourses of Instruction\n"
        "GEOL 410 Economic Geology\nStudy of mineral deposits.\n3 credits.",
        "SYNTHETIC UNIVERSITY - TEST FIXTURE\nCourses of Instruction\n"
        "GEOL 420 Ore Deposit Geology\n" + body + "\n3 credits.",
        "SYNTHETIC UNIVERSITY - TEST FIXTURE\nDegree Requirements\n"
        "Choose one elective:\nGEOL 420 Ore Deposit Geology\nThis is a requirements reference.",
    ]
    with fitz.open() as pdf:
        for text in printed:
            page = pdf.new_page()
            page.insert_text((55, 75), text, fontsize=12)
        pdf.save(pdf_path)
    with fitz.open(pdf_path) as pdf:
        texts = [page.get_text("text") for page in pdf]
    checksum = sha256(pdf_path)
    stream_path = root / "source.jsonl.gz"
    with gzip.open(stream_path, "wt", encoding="utf-8") as stream:
        for index, raw in enumerate(texts):
            stream.write(
                json.dumps(
                    {
                        "search_source_id": "pdf_synthetic",
                        "document_id": "doc_demo",
                        "source_pdf_checksum": checksum,
                        "page_index": index,
                        "page_number": index + 1,
                        "raw_text": raw,
                        "extraction_type": "embedded",
                    }
                )
                + "\n"
            )
    vocabulary_path = root / "vocabulary.json"
    write_json(vocabulary_path, reference.matcher.build_vocabulary())
    specifications = [
        (2, "ore deposit", "Ore Deposit", "Ore Deposit"),
        (2, "ore deposit geology", "Ore Deposit Geology", "Ore Deposit Geology"),
        (2, "mineral system", "mineral systems", "mineral systems"),
        (3, "ore deposit", "Ore Deposit", "Ore Deposit"),
        (3, "ore deposit geology", "Ore Deposit Geology", "Ore Deposit Geology"),
    ]
    if corrupt_lexical_quote:
        specifications.append((2, "ore deposit", "ore deposits", "ore dep0sits"))
    else:
        specifications.append(
            (2, "ore-forming process", "ore-forming processes", "ore-forming processes")
        )
    hits = []
    for index, (number, concept, form, fragment) in enumerate(specifications, 1):
        start = texts[number - 1].index(fragment)
        hits.append(
            {
                "page_task_id": f"task_p{number}",
                "hit_id": f"demo_hit_{index}",
                "occurrence_id": f"demo_occurrence_{index}",
                "search_source_id": "pdf_synthetic",
                "document_id": "doc_demo",
                "physical_page": str(number),
                "canonical_concept": concept,
                "matched_supervisor_form": form,
                "source_spans_json": json.dumps(
                    [
                        {
                            "page_index": number - 1,
                            "raw_start": start,
                            "raw_end": start + len(fragment),
                        }
                    ]
                ),
                "structural_priority": "10",
                "structural_candidate_type": "UNADJUDICATED",
                "source_review_status": "PENDING_SOURCE_REVIEW",
            }
        )
    tasks = []
    for rank, number in enumerate((2, 3), 1):
        ids = [hit["hit_id"] for hit in hits if hit["physical_page"] == str(number)]
        tasks.append(
            {
                "review_rank": str(rank),
                "page_task_id": f"task_p{number}",
                "ownership_packet_id": f"packet_p{number}",
                "search_source_id": "pdf_synthetic",
                "document_id": "doc_demo",
                "source_pdf_checksum": checksum,
                "institution_id_reported": "synthetic",
                "institution_reported": "Synthetic University",
                "catalog_year_reported": "1950",
                "metadata_grouping_status": "SYNTHETIC",
                "central_physical_page": str(number),
                "previous_physical_page": str(number - 1),
                "next_physical_page": str(number + 1) if number < 3 else "",
                "source_pdf_path": str(pdf_path),
                "page_stream_path": str(stream_path),
                "page_stream_sha256": sha256(stream_path),
                "hit_count": str(len(ids)),
                "hit_ids_json": json.dumps(ids),
                "review_order_score": "10",
                "structural_candidate_types_json": '["UNADJUDICATED"]',
                "coarse_region_type": "UNKNOWN",
                "geometry_requirement": "REQUIRED",
                "review_status": "PENDING_SOURCE_REVIEW",
            }
        )
    decisions = [
        {"page_task_id": hit["page_task_id"], "hit_id": hit["hit_id"], "decision": ""}
        for hit in hits
    ]
    for name, rows in (
        ("page_review_tasks.csv", tasks),
        ("task_hit_crosswalk.csv", hits),
        ("hit_disposition_template.csv", decisions),
    ):
        write_csv(release / name, rows, list(rows[0]))
    write_json(
        release / "task_release_manifest.json",
        {
            "release_id": "supervisor-keyword-v2-page-review-tasks-v1",
            "fixture_only": True,
            "page_tasks": 2,
            "original_physical_hits": len(hits),
            "hits_accounted_exactly_once": True,
            "ownership_truth_assigned": False,
            "ranking_is_truth": False,
            "all_low_ranked_hits_retained": True,
            "files_sha256": {
                name: sha256(release / name)
                for name in (
                    "page_review_tasks.csv",
                    "task_hit_crosswalk.csv",
                    "hit_disposition_template.csv",
                )
            },
        },
    )
    return Fixture(root, release, vocabulary_path, pdf_path, stream_path, tasks, hits)
