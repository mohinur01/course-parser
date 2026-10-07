# Tests for review routing that preserves existing proposals and never approves publication.
"""Precision routing cannot create courses, hide uncertainty or authorize publication."""

import copy
from pathlib import Path

import pytest
from test_ownership_links import linked_fixture
from test_source_learning import _pilot_fixture

from course_parser.io import read_json, read_jsonl
from course_parser.learning import run_learning_baseline
from course_parser.page_accounting import build_page_accounting
from course_parser.precision_audit import coverage_metrics
from course_parser.precision_policy import audit_routes, route_window


# Build a small test input with the source identity and values required by this test group.
def fixture(reference):
    courses, graph, candidates, decisions, blocks, _, _ = linked_fixture(reference)
    metadata = {
        "dataset_page_id": "fixture-window",
        "central_physical_page": 1,
        "context_physical_pages": [1],
        "source_pdf_checksum": graph.identity.source_pdf_checksum,
    }
    proposal = {
        "dataset_page_id": metadata["dataset_page_id"],
        "status": "PROPOSED",
        "source_truth_read": False,
        "candidates": [x.model_dump(mode="json") for x in candidates],
        "decisions": [x.model_dump(mode="json") for x in decisions],
        "blocks": [x.model_dump(mode="json") for x in blocks],
    }
    headers = {c.candidate_id: c for c in candidates}
    records = [
        {
            "dataset_page_id": metadata["dataset_page_id"],
            "header_physical_page": headers[c.header_candidate_id].physical_page,
            "scope": "CENTRAL",
            "course": c.model_dump(mode="json"),
        }
        for c in courses
    ]
    accounting = build_page_accounting(
        graph, blocks, courses, candidates=candidates, decisions=decisions
    )
    return metadata, proposal, records, accounting


# Check that resolved core and creditless course are proposals with publication review.
def test_resolved_core_and_creditless_course_are_proposals_with_publication_review(reference):
    args = fixture(reference)
    original = copy.deepcopy(args)
    routes = route_window(*args)
    assert args == original
    assert len(routes) == 2
    assert {r["band"] for r in routes} == {"HIGH"}
    assert all(r["action"] == "AUTO_PROPOSAL_ELIGIBLE" for r in routes)
    assert all(r["human_review_required"] and not r["publication_authorized"] for r in routes)
    assert all(r["review_decision"] is None and r["calibrated_probability"] is None for r in routes)
    assert routes[1]["metadata_review_reasons"]  # missing printed credits still need field review
    assert audit_routes(*args, routes)["status"] == "PASS"


# Check that unsafe core never high even with perfect numeric confidence.
@pytest.mark.parametrize(
    "mutation",
    [
        "header",
        "header_evidence",
        "context",
        "support",
        "contradiction",
        "boundary",
        "block_boundary",
        "continuation",
        "block_continuation",
        "code",
        "title",
        "description",
        "partial_description",
        "warning",
        "ambiguous_metadata",
    ],
)
def test_unsafe_core_never_high_even_with_perfect_numeric_confidence(reference, mutation):
    m, p, rs, a = fixture(reference)
    c = rs[0]["course"]
    header = next(h for h in p["candidates"] if h["candidate_id"] == c["header_candidate_id"])
    decision = next(d for d in p["decisions"] if d["candidate_id"] == c["header_candidate_id"])
    block = next(b for b in p["blocks"] if b["block_id"] == c["block_id"])
    header["confidence"] = decision["confidence"] = 1.0
    for name in ("code", "title", "description", "credits", "prerequisites"):
        c[name]["confidence"] = 1.0
    if mutation == "header":
        header["status"] = "UNRESOLVED"
    elif mutation == "header_evidence":
        header["evidence"] = []
    elif mutation == "context":
        decision["context_status"] = "UNRESOLVED"
    elif mutation == "support":
        decision["supporting_node_ids"] = []
    elif mutation == "contradiction":
        decision["contradictions"] = ["REQUIREMENT_CONTEXT"]
    elif mutation == "boundary":
        c["boundary_status"] = "UNRESOLVED_BOUNDARY"
    elif mutation == "block_boundary":
        block["boundary_status"] = "UNRESOLVED_BOUNDARY"
    elif mutation == "continuation":
        c["continuation_status"] = "UNRESOLVED_CONTINUATION"
    elif mutation == "block_continuation":
        block["continuation_status"] = "UNRESOLVED_CONTINUATION"
    elif mutation in ("code", "title", "description"):
        c[mutation]["status"] = "AMBIGUOUS"
    elif mutation == "partial_description":
        c["description_status"] = "PARTIAL_DESCRIPTION_VISIBLE"
    elif mutation == "warning":
        c["warnings"] = [{"code": "REVIEW"}]
    else:
        c["credits"]["status"] = "AMBIGUOUS"
    route = next(
        r for r in route_window(m, p, rs, a) if r["candidate_id"] == header["candidate_id"]
    )
    assert route["band"] == "MEDIUM"
    assert route["existing_record_retained"] and route["human_review_required"]
    assert not route["publication_authorized"]


# Check that missing record or negative decision cannot create a course.
@pytest.mark.parametrize(
    "status,band,action",
    [
        ("INDEPENDENT_COURSE", "MEDIUM", "SOURCE_REVIEW_ONLY"),
        ("UNRESOLVED", "LOW", "OMISSION_QUESTION_ONLY"),
        ("COURSE_REFERENCE", "EXCLUDED", "REFERENCE_OR_NONCOURSE_AUDIT"),
        ("NONCOURSE", "EXCLUDED", "REFERENCE_OR_NONCOURSE_AUDIT"),
    ],
)
def test_missing_record_or_negative_decision_cannot_create_a_course(
    reference, status, band, action
):
    m, p, rs, a = fixture(reference)
    cid = rs[0]["course"]["header_candidate_id"]
    rs = rs[1:]
    decision = next(d for d in p["decisions"] if d["candidate_id"] == cid)
    decision.update(status=status, confidence=1.0)
    route = next(r for r in route_window(m, p, rs, a) if r["candidate_id"] == cid)
    assert route["band"] == band and route["action"] == action
    assert not route["existing_record_retained"] and route["course_occurrence_id"] is None
    assert route["review_decision"] is None and not route["publication_authorized"]


# Reject proposals whose source identity or text-ownership checks fail.
@pytest.mark.parametrize(
    "mutation",
    [
        "publication",
        "duplicate_record",
        "duplicate_candidate",
        "missing_decision",
        "source",
        "scope",
        "negative_owner",
        "block",
    ],
)
def test_invalid_source_and_ownership_contracts_fail_closed(reference, mutation):
    m, p, rs, a = fixture(reference)
    if mutation == "publication":
        rs[0]["course"]["publication_status"] = "AUTO_ACCEPTED"
    elif mutation == "duplicate_record":
        rs.append(copy.deepcopy(rs[0]))
    elif mutation == "duplicate_candidate":
        p["candidates"].append(copy.deepcopy(p["candidates"][0]))
    elif mutation == "missing_decision":
        p["decisions"].pop()
    elif mutation == "source":
        rs[0]["course"]["source"]["source_pdf_checksum"] = "b" * 64
    elif mutation == "scope":
        rs[0]["scope"] = "CONTEXT"
    elif mutation == "negative_owner":
        next(
            d for d in p["decisions"] if d["candidate_id"] == rs[0]["course"]["header_candidate_id"]
        )["status"] = "COURSE_REFERENCE"
    else:
        rs[0]["course"]["block_id"] = "invented"
    with pytest.raises(ValueError):
        route_window(m, p, rs, a)


# Catch altered routes and prefilled review answers when independently checking the routing
# output.
@pytest.mark.parametrize(
    "key,value",
    [
        ("publication_authorized", True),
        ("human_review_required", False),
        ("review_decision", "APPROVED"),
        ("candidate_id", "invented"),
        ("band", "LOW"),
    ],
)
def test_mutated_routing_and_filled_review_answers_fail_independent_audit(reference, key, value):
    args = fixture(reference)
    routes = route_window(*args)
    routes[0][key] = value
    with pytest.raises(ValueError, match="source-only policy"):
        audit_routes(*args, routes)


# Check that source confirmed description absence is distinct from missing description.
def test_source_confirmed_description_absence_is_distinct_from_missing_description(reference):
    m, p, rs, a = fixture(reference)
    c = rs[0]["course"]
    c["description_status"] = "SOURCE_HAS_NO_DESCRIPTION"
    c["description"].update(raw=None, status="ABSENT_IN_SOURCE")
    cid = c["header_candidate_id"]
    assert next(r for r in route_window(m, p, rs, a) if r["candidate_id"] == cid)["band"] == "HIGH"
    c["description"]["status"] = "NOT_RECOVERED"
    assert (
        next(r for r in route_window(m, p, rs, a) if r["candidate_id"] == cid)["band"] == "MEDIUM"
    )


# Check that runtime sidecars preserve records and fail atomically on policy error.
@pytest.mark.parametrize("contextual", [False, True])
def test_runtime_sidecars_preserve_records_and_fail_atomically_on_policy_error(
    tmp_path, reference, monkeypatch, contextual
):
    from course_parser import learning

    pilot, _, _, _ = _pilot_fixture(tmp_path, reference)
    good = tmp_path / "good"
    run_learning_baseline(reference, pilot, good, contextual_course_assembly=contextual)
    p = read_jsonl(good / "page_proposals.jsonl")[0]
    records = read_jsonl(good / "course_proposals.jsonl")
    routes = read_jsonl(good / "precision_routes.jsonl")
    assert p["precision_routes"] == routes
    assert sum(r["existing_record_retained"] for r in routes) == len(records)
    assert read_json(good / "prediction_manifest.json")["automatic_publication_authorized"] is False
    assert all(r["course"]["publication_status"] == "REVIEW_REQUIRED" for r in records)

    # Raise a deliberate error to test how the caller retains evidence after a failure.
    def fail(*args, **kwargs):
        raise ValueError("deliberate precision policy failure")

    monkeypatch.setattr(learning, "route_window", fail)
    bad = tmp_path / "bad"
    run_learning_baseline(reference, pilot, bad, contextual_course_assembly=contextual)
    assert not read_jsonl(bad / "course_proposals.jsonl")
    assert not read_jsonl(bad / "precision_routes.jsonl")
    assert read_jsonl(bad / "page_proposals.jsonl")[0]["status"] == "PARSER_STAGE_FAILED"
    assert read_jsonl(bad / "precision_window_failures.jsonl")
    assert (
        read_jsonl(bad / "page_accounting.jsonl")[0]["whole_page_accounting"]["audit"]["status"]
        == "PASS"
    )


# Check that fixed coverage keeps unknown alignment false positive and zero denominators.
def test_fixed_coverage_keeps_unknown_alignment_false_positive_and_zero_denominators():
    routes = [
        {
            "dataset_page_id": "p",
            "candidate_id": str(i),
            "scope": "CENTRAL",
            "existing_record_retained": True,
            "band": band,
        }
        for i, band in enumerate(("HIGH", "HIGH", "HIGH", "MEDIUM"))
    ]
    labels = [
        {"dataset_page_id": "p", "candidate_id": str(i), "label": label}
        for i, label in ((0, 1), (1, 0), (3, 1))
    ]
    high, both = coverage_metrics(routes, labels)
    assert high["reviewed_header_precision"] == 0.5
    assert high["proposal_coverage"] == 0.75
    assert high["unlabeled_or_ambiguous_proposals"] == 1
    assert high["observed_positive_share_of_all_selected"] == 1 / 3
    assert both["proposal_coverage"] == 1.0 and both["reviewed_header_false_positives"] == 1
    assert all(
        r["reviewed_header_precision"] is None and r["proposal_coverage"] is None
        for r in coverage_metrics([], [])
    )
    with pytest.raises(ValueError):
        coverage_metrics(routes, labels + labels[:1])
    with pytest.raises(ValueError):
        coverage_metrics(routes, [{"dataset_page_id": "other", "candidate_id": "0", "label": 1}])


# Reject reserved inputs before opening their contents.
@pytest.mark.parametrize("argument", ["pilot", "evaluation", "output"])
def test_reserved_inputs_rejected_before_any_payload_read(tmp_path, monkeypatch, argument):
    from course_parser import precision_audit as module

    # Fail the test if a guarded operation attempts to read a prohibited payload.
    def forbidden(*args, **kwargs):
        pytest.fail("payload read before reserved guard")

    monkeypatch.setattr(module, "read_json", forbidden)
    monkeypatch.setattr(module, "read_jsonl", forbidden)
    paths = {key: tmp_path / key for key in ("pilot", "evaluation", "output")}
    paths[argument] = Path("artifacts/validation-transfer-v1/heldout-review")
    with pytest.raises(ValueError, match="reserved"):
        module.audit_precision(None, **paths)


# Reject an unregistered evaluation before reading cached source text.
def test_unregistered_evaluation_rejected_before_cached_source_read(tmp_path, monkeypatch):
    from course_parser import precision_audit as module

    # Fail the test if a guarded operation attempts to read a prohibited payload.
    def forbidden(*args, **kwargs):
        pytest.fail("source read before exposed registration")

    monkeypatch.setattr(module, "read_jsonl", forbidden)
    with pytest.raises(ValueError):
        module.audit_precision(
            None, tmp_path / "pilot", tmp_path / "unregistered", tmp_path / "out"
        )


# Check that cached measurement routes before labels and rejects source checksum drift.
@pytest.mark.parametrize("tamper", [False, True])
def test_cached_measurement_routes_before_labels_and_rejects_source_checksum_drift(
    tmp_path, reference, monkeypatch, tamper
):
    from course_parser import precision_audit as module
    from course_parser.io import artifact_hashes, sha256, write_json, write_jsonl

    m, p, rs, a = fixture(reference)
    m["learning_partition"] = "TUNING"
    pilot, evaluation, predictions = (tmp_path / name for name in ("pilot", "eval", "pred"))
    for folder in (pilot, evaluation, predictions):
        folder.mkdir()
    write_json(pilot / "pilot_manifest.json", {"test": True})
    write_jsonl(pilot / "selected_pages.jsonl", [m])
    write_jsonl(predictions / "page_proposals.jsonl", [p])
    write_jsonl(predictions / "course_proposals.jsonl", rs)
    write_json(
        predictions / "prediction_manifest.json",
        {
            "source_truth_read": False,
            "protected_evaluation_opened": False,
            "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
            "page_windows": 1,
            "file_sha256": artifact_hashes(predictions),
        },
    )
    write_jsonl(
        evaluation / "page_metrics.jsonl",
        [{"dataset_page_id": m["dataset_page_id"], "status": "SCORED_DEVELOPMENT"}],
    )
    write_jsonl(
        evaluation / "entry_training_examples.jsonl",
        [
            {
                "dataset_page_id": m["dataset_page_id"],
                "candidate_id": rs[0]["course"]["header_candidate_id"],
                "label": 1,
            }
        ],
    )
    write_json(
        evaluation / "evaluation_manifest.json",
        {
            "prediction_path": str(predictions),
            "prediction_manifest_sha256": sha256(predictions / "prediction_manifest.json"),
            "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(evaluation),
            "overall": {"false_course_proposals": 0},
        },
    )
    # The real registry and typed accounting each have independent integration tests.
    # Here synthetic approved metadata isolates cache binding and read order.
    monkeypatch.setattr(module, "load_exposed_split_metadata", lambda *args: [m])
    monkeypatch.setattr(module, "account_cached_window", lambda *args: a)
    monkeypatch.setattr(module, "bind_records", lambda *args: {"role_counts": {"TUNING": 1}})
    original_audit, reader = module.audit_routes, module.read_jsonl
    audited = []

    # Report the release counts and preserved original-hit accounting.
    def audit(*args):
        result = original_audit(*args)
        audited.append(True)
        return result

    # Assert that source-only routing occurred before any test review labels are read.
    def read(path):
        if path.parent == evaluation:
            assert audited, "labels opened before source-only routing"
        return reader(path)

    monkeypatch.setattr(module, "audit_routes", audit)
    monkeypatch.setattr(module, "read_jsonl", read)
    if tamper:
        (predictions / "page_proposals.jsonl").write_text("invalid changed source")
        with pytest.raises(ValueError, match="checksum changed"):
            module.audit_precision(reference, pilot, evaluation, tmp_path / "out")
        assert not audited
    else:
        result = module.audit_precision(reference, pilot, evaluation, tmp_path / "out")
        assert result["existing_records_retained"] == 2
        assert result["precision_coverage"][0]["unlabeled_or_ambiguous_proposals"] == 1
        assert not result["automatic_publication_authorized"]


# Check that numeric scores keywords and external answers cannot change routing.
def test_numeric_scores_keywords_and_external_answers_cannot_change_routing(reference):
    m, p, rs, a = fixture(reference)
    expected = route_window(m, p, rs, a)
    for value in (0.0, 1.0):
        for candidate in p["candidates"]:
            candidate["confidence"] = value
            candidate["external_expected_label"] = "COURSE"
        for decision in p["decisions"]:
            decision["confidence"] = value
        for record in rs:
            record["keyword_matches"] = [{"pretend_approval": True}]
            for name in ("code", "title", "description", "credits", "prerequisites"):
                record["course"][name]["confidence"] = value
        m["institution"] = "untrusted policy suggestion"
        assert route_window(m, p, rs, a) == expected
