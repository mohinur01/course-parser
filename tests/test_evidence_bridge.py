# Tests for linking original keyword hits to their checked PDF page evidence.
from __future__ import annotations

import json

import pytest
from fixtures import create_fixture

from course_parser.bridge import INCOMPLETE, READY, prepare_bridge, verify_run
from course_parser.cli import main
from course_parser.io import contained_file, read_csv, read_json, read_jsonl, sha256, write_csv
from course_parser.release import TaskRelease


# Build a small test input with the source identity and values required by this test group.
def setup_batch(tmp_path, reference, *, corrupt_lexical_quote=False):
    fixture = create_fixture(
        tmp_path / "fixture", reference, corrupt_lexical_quote=corrupt_lexical_quote
    )
    release = TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    packet = tmp_path / "packet"
    reference.materialize(fixture.release, packet, start_rank=1, count=2)
    return fixture, release, packet


# Check that end to end preserves overlaps context and pending hits.
def test_end_to_end_preserves_overlaps_context_and_pending_hits(tmp_path, reference):
    fixture, release, packet = setup_batch(tmp_path, reference)
    output = tmp_path / "evidence"
    result = prepare_bridge(reference, release, packet, output)
    assert result["status"] == "READY_FOR_SOURCE_REVIEW"
    assert result["selected_original_hits"] == 6
    assert result["unique_context_pages"] == 3
    hits = read_jsonl(output / "hit_evidence.jsonl")
    assert {row["hit_id"] for row in hits} == {row["hit_id"] for row in fixture.hits}
    assert all(row["decision"] == "" and row["proposed_owner"] is None for row in hits)
    short, long = hits[:2]
    assert short["source_quote"] == "Ore Deposit"
    assert long["source_quote"] == "Ore Deposit Geology"
    assert (
        short["v3_evidence_anchors"][0]["text_span"]["start"]
        == long["v3_evidence_anchors"][0]["text_span"]["start"]
    )
    assert short["original_source_spans"] == json.loads(fixture.hits[0]["source_spans_json"])
    assert all(row["decision"] == "" for row in read_csv(output / "hit_disposition_template.csv"))
    assert not result["complete_source_documents"]
    assert not result["catalog_negative_inference_allowed"]
    assert verify_run(output, recheck_inputs=True)["status"] == "PASS"
    for page in read_jsonl(output / "page_evidence.jsonl"):
        typed = reference.models.PageSource.model_validate(page["v3_page_source"])
        assert typed.image_sha256 == page["image_sha256"]
        for hit in hits:
            for value in hit["v3_evidence_anchors"]:
                if value["physical_page"] == typed.physical_page:
                    reference.models.EvidenceAnchor.model_validate(value).validate_against(
                        typed.representations[0]
                    )


# Check that bad context image blocks only affected tasks.
@pytest.mark.parametrize("failure", ["missing", "wrong_pixels", "unreadable"])
def test_bad_context_image_blocks_only_affected_tasks(tmp_path, reference, failure):
    _, release, packet = setup_batch(tmp_path, reference)
    image = packet / "assets/pdf_synthetic_p00001.png"
    if failure == "missing":
        image.unlink()
    elif failure == "unreadable":
        image.write_bytes(b"not a PNG")
    else:
        image.write_bytes((packet / "assets/pdf_synthetic_p00002.png").read_bytes())
    output = tmp_path / "evidence"
    result = prepare_bridge(reference, release, packet, output)
    assert result["status"] == INCOMPLETE
    assert result["task_status_counts"] == {INCOMPLETE: 1, READY: 1}
    assert result["selected_original_hits"] == result["pending_dispositions"] == 6
    assert all(row["decision"] == "" for row in read_jsonl(output / "hit_evidence.jsonl"))
    assert verify_run(output)["batch_status"] == INCOMPLETE


# Check that changed source stream is not used.
def test_changed_source_stream_is_not_used(tmp_path, reference):
    fixture, release, packet = setup_batch(tmp_path, reference)
    fixture.stream.write_bytes(b"a different frozen source")
    result = prepare_bridge(reference, release, packet, tmp_path / "evidence")
    assert result["status"] == INCOMPLETE
    assert result["task_status_counts"] == {INCOMPLETE: 2}
    assert result["selected_original_hits"] == 6


# Check that bad original quote is not OCR corrected.
def test_bad_original_quote_is_not_ocr_corrected(tmp_path, reference):
    _, release, packet = setup_batch(tmp_path, reference, corrupt_lexical_quote=True)
    output = tmp_path / "evidence"
    result = prepare_bridge(reference, release, packet, output)
    assert result["status"] == INCOMPLETE
    bad = next(
        row
        for row in read_jsonl(output / "hit_evidence.jsonl")
        if row["source_quote"] == "ore dep0sits"
    )
    assert bad["decision"] == ""
    assert "claimed lexical hit" in bad["errors"][0]


# Check that missing PDF is retained in a completed gap report.
def test_missing_pdf_is_retained_in_a_completed_gap_report(tmp_path, reference):
    fixture = create_fixture(tmp_path / "fixture", reference)
    release = TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    fixture.pdf.unlink()
    packet = tmp_path / "packet"
    reference.materialize(fixture.release, packet, start_rank=1, count=2)
    result = prepare_bridge(reference, release, packet, tmp_path / "evidence")
    assert result["status"] == INCOMPLETE
    assert result["selected_original_hits"] == 6
    assert result["finalized_dispositions"] == 0


# Check that changed packet manifest and output artifact are rejected.
def test_changed_packet_manifest_and_output_artifact_are_rejected(tmp_path, reference):
    _, release, packet = setup_batch(tmp_path, reference)
    output = tmp_path / "evidence"
    prepare_bridge(reference, release, packet, output)
    (output / "hit_evidence.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="checksum changed"):
        verify_run(output)
    (packet / "batch_task_status.csv").write_text("different packet\n")
    with pytest.raises(ValueError, match="checksum changed"):
        prepare_bridge(reference, release, packet, tmp_path / "other")
    assert not (tmp_path / "other").exists()


# Refuse to overwrite a previously saved output.
def test_output_refuses_overwrite(tmp_path, reference):
    _, release, packet = setup_batch(tmp_path, reference)
    output = tmp_path / "evidence"
    prepare_bridge(reference, release, packet, output)
    original = (output / "run_manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare_bridge(reference, release, packet, output)
    assert (output / "run_manifest.json").read_bytes() == original


# Check that run semantics cannot be bypassed by rehashing.
@pytest.mark.parametrize("change", ["anchor", "coverage", "template"])
def test_run_semantics_cannot_be_bypassed_by_rehashing(tmp_path, reference, change):
    _, release, packet = setup_batch(tmp_path, reference)
    output = tmp_path / "evidence"
    prepare_bridge(reference, release, packet, output)
    if change == "anchor":
        path = output / "hit_evidence.jsonl"
        rows = read_jsonl(path)
        rows[0]["v3_evidence_anchors"][0]["observed_text"] = "invented text"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif change == "coverage":
        path = output / "coverage.json"
        value = read_json(path)
        value["catalog_negative_inference_allowed"] = True
        path.write_text(json.dumps(value))
    else:
        path = output / "hit_disposition_template.csv"
        rows = read_csv(path)
        rows[0]["decision"] = "NONCOURSE"
        path.unlink()
        write_csv(path, rows, list(rows[0]))
    manifest_path = output / "run_manifest.json"
    manifest = read_json(manifest_path)
    manifest["files_sha256"][path.name] = sha256(path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_run(output)


# Check that release accounting cannot be bypassed by rehashing.
@pytest.mark.parametrize("change", ["duplicate_hit", "missing_hit", "unapproved_form", "bad_page"])
def test_release_accounting_cannot_be_bypassed_by_rehashing(tmp_path, reference, change):
    fixture = create_fixture(tmp_path / "fixture", reference)
    rows = fixture.hits.copy()
    rows = [dict(row) for row in rows]
    if change == "duplicate_hit":
        rows[1]["hit_id"] = rows[0]["hit_id"]
    elif change == "missing_hit":
        rows.pop()
    elif change == "unapproved_form":
        rows[0]["matched_supervisor_form"] = "ore dep0sit"
    else:
        rows[0]["source_spans_json"] = '[{"page_index":99,"raw_start":0,"raw_end":11}]'
    path = fixture.release / "task_hit_crosswalk.csv"
    path.unlink()
    write_csv(path, rows, list(rows[0]))
    fixture.refresh_release()
    with pytest.raises(ValueError):
        TaskRelease.load(fixture.release, fixture.vocabulary, reference)


# Check that semicolon labels are not confused with source quotes.
def test_semicolon_labels_are_not_confused_with_source_quotes(tmp_path, reference):
    fixture = create_fixture(tmp_path / "fixture", reference)
    rows = [dict(row) for row in fixture.hits]
    rows[0]["matched_supervisor_form"] = "ore deposit;Ore deposit"
    path = fixture.release / "task_hit_crosswalk.csv"
    path.unlink()
    write_csv(path, rows, list(rows[0]))
    fixture.refresh_release()
    release = TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    assert release.hits["task_p2"][0]["matched_supervisor_form"] == "ore deposit;Ore deposit"


# Check that renderer identifiers cannot escape artifact directory.
@pytest.mark.parametrize("key", ["page_task_id", "search_source_id"])
def test_renderer_identifiers_cannot_escape_artifact_directory(tmp_path, reference, key):
    fixture = create_fixture(tmp_path / "fixture", reference)
    rows = [dict(row) for row in fixture.tasks]
    rows[0][key] = "../outside"
    path = fixture.release / "page_review_tasks.csv"
    path.unlink()
    write_csv(path, rows, list(rows[0]))
    fixture.refresh_release()
    with pytest.raises(ValueError, match="unsafe artifact identifier"):
        TaskRelease.load(fixture.release, fixture.vocabulary, reference)


# Check that modified vocabulary and traversal are rejected.
def test_modified_vocabulary_and_traversal_are_rejected(tmp_path, reference):
    fixture = create_fixture(tmp_path / "fixture", reference)
    value = read_json(fixture.vocabulary)
    value["concepts"][0]["pattern"] = ".*"
    fixture.vocabulary.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="authoritative reference matcher"):
        TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    with pytest.raises(ValueError, match="escapes"):
        contained_file(tmp_path, "../outside.json")


# Check that matcher keeps fields separate and has no fuzzy correction.
def test_matcher_keeps_fields_separate_and_has_no_fuzzy_correction(reference):
    vocabulary = reference.matcher.build_vocabulary()
    assert not reference.matcher.match_text("Ore", vocabulary)
    assert not reference.matcher.match_text("deposit geology", vocabulary)
    assert not reference.matcher.match_text("ore dep0sits", vocabulary)
    assert reference.matcher.match_text("MINERAL\nDEPOSITS", vocabulary)


# Build an example through the command line, then verify its saved files.
def test_cli_build_and_verification(tmp_path, reference, capsys):
    fixture = create_fixture(tmp_path / "fixture", reference)
    output = tmp_path / "cli-batch"
    args = [
        "build-batch",
        "--reference-project",
        str(reference.root),
        "--task-release",
        str(fixture.release),
        "--vocabulary",
        str(fixture.vocabulary),
        "--start-rank",
        "1",
        "--count",
        "2",
        "--output",
        str(output),
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["selected_original_hits"] == 6
    assert main(["verify-run", "--run", str(output / "evidence"), "--recheck-inputs"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "FAILED"


# Display source metadata as plain text rather than allowing it to become HTML markup.
def test_report_escapes_source_metadata(tmp_path, reference):
    fixture, release, packet = setup_batch(tmp_path, reference)
    release.tasks[0]["institution_reported"] = '<script>alert("unsafe")</script>'
    output = tmp_path / "evidence"
    prepare_bridge(reference, release, packet, output)
    content = (output / "index.html").read_text()
    assert "<script>alert" not in content
    assert "&lt;script&gt;" in content
