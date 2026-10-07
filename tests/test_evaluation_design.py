# Tests for keeping institution families and protected evaluation data out of development
# inputs.
"""Exposure and transitive source dependencies cannot disappear behind split labels."""

import copy
from pathlib import Path

import pytest

from course_parser import evaluation_design as module
from course_parser.entry_learning import fit_ranker, train_entry_ranker
from course_parser.io import artifact_hashes, read_json, sha256, write_json, write_jsonl


# Build a small test input with the source identity and values required by this test group.
def page(name="p1", partition="TUNING", **changes):
    return {
        "dataset_page_id": name,
        "split": "DEVELOPMENT",
        "learning_group": "group-" + name,
        "institution_id": "institution-" + name,
        "learning_partition": partition,
        "source_pdf_checksum": ("a" if name == "p1" else "b") * 64,
        "central_physical_page": 2,
        "context_physical_pages": [1, 2, 3],
        **changes,
    }


# Check that exposed validation is diagnostic without rewriting legacy tags.
def test_exposed_validation_is_diagnostic_without_rewriting_legacy_tags():
    rows = [page(), page("p2", "VALIDATION")]
    frozen = copy.deepcopy(rows)
    result = module.audit_split_metadata(rows)
    assert rows == frozen
    assert result["role_counts"] == {"TUNING": 1, "EXPOSED_DIAGNOSTIC": 1}
    assert result["pages"][1]["legacy_partition"] == "VALIDATION"
    assert not result["generalization_claim_permitted"]
    assert not result["protected_cohort_isolation_verified"]


# Check that alternative family ids cannot hide cross role dependencies.
@pytest.mark.parametrize(
    "changes",
    [
        {"learning_group": "group-p1"},
        {"institution_id": "institution-p1", "catalog_year": 1983},
        {"institution_alias_ids": ["institution-p1"]},
        {"original_reported_identity": {"institution_id": "institution-p1"}},
        {
            "source_pdf_checksum": "a" * 64,
            "central_physical_page": 6,
            "context_physical_pages": [5, 6, 7],
        },
    ],
)
def test_alternative_family_ids_cannot_hide_cross_role_dependencies(changes):
    with pytest.raises(ValueError, match="family crosses"):
        module.audit_split_metadata([page(catalog_year=1982), page("p2", "VALIDATION", **changes)])


# Check that declared template family cannot cross institutions and roles.
def test_declared_template_family_cannot_cross_institutions_and_roles():
    with pytest.raises(ValueError, match="family crosses"):
        module.audit_split_metadata(
            [
                page(catalog_family_id="template-x"),
                page("p2", "VALIDATION", catalog_family_id="template-x"),
            ]
        )


# Check that dependency union is transitive and order independent.
def test_dependency_union_is_transitive_and_order_independent():
    rows = [
        page(institution_alias_ids=["alias-A"]),
        page("p2", institution_alias_ids=["alias-A", "alias-B"]),
        page("p3", institution_alias_ids=["alias-B"], source_pdf_checksum="c" * 64),
    ]
    assert module.audit_split_metadata(rows)["connected_families"] == 1
    assert module.audit_split_metadata(list(reversed(rows)))["connected_families"] == 1
    rows[-1]["learning_partition"] = "VALIDATION"
    with pytest.raises(ValueError, match="family crosses"):
        module.audit_split_metadata(rows)


# Check that overlapping context windows are counted once and not independent.
def test_overlapping_context_windows_are_counted_once_and_not_independent():
    rows = [
        page(),
        page(
            "p2",
            source_pdf_checksum="a" * 64,
            central_physical_page=3,
            context_physical_pages=[2, 3, 4],
        ),
    ]
    result = module.audit_split_metadata(rows)
    assert result["windows"] == 2
    assert result["connected_families"] == 1
    assert result["unique_physical_context_pages"] == 4
    assert result["overlapping_context_window_pairs"] == 1
    rows[-1]["learning_partition"] = "VALIDATION"
    with pytest.raises(ValueError, match="family crosses"):
        module.audit_split_metadata(rows)


# Check that duplicate physical page is not two evaluation observations.
def test_duplicate_physical_page_is_not_two_evaluation_observations():
    with pytest.raises(ValueError, match="duplicate physical"):
        module.audit_split_metadata([page(), page("p2", source_pdf_checksum="a" * 64)])


# Keep protected evaluation roles unavailable during development.
@pytest.mark.parametrize("partition", ["FRESH_VALIDATION", "TEST", "CALIBRATION"])
def test_protected_roles_refused_during_phases(partition):
    with pytest.raises(ValueError, match="only TUNING"):
        module.audit_split_metadata([page(partition=partition)])


# Check that context cannot be invented or deduplicated silently.
@pytest.mark.parametrize("context", [[1, 1, 2], [0, 1, 2], [1, True, 2], [1, 3]])
def test_context_cannot_be_invented_or_deduplicated_silently(context):
    with pytest.raises(ValueError, match="invalid physical context"):
        module.audit_split_metadata([page(context_physical_pages=context)])


# Prevent protected data from becoming a development input just by changing its split or role
# label.
def test_protected_split_and_role_relabeling_refused():
    with pytest.raises(ValueError, match="protected page"):
        module.audit_split_metadata([page(split="TEST")])
    with pytest.raises(ValueError, match="contradicts"):
        module.audit_split_metadata([page(evaluation_role="EXPOSED_DIAGNOSTIC")])


# Keep split-checking metadata free of review answers, source text, images and file paths.
def test_metadata_projection_excludes_answers_text_images_and_paths():
    projected = module.split_metadata(
        page(
            description="never read",
            answers={"x": 1},
            image_path="never open",
            source_pdf_path="never open",
        )
    )
    assert not {"description", "answers", "image_path", "source_pdf_path"} & projected.keys()
    result = module.audit_split_metadata([projected])
    assert result["declared_catalog_family_windows"] == 0
    assert result["unknown_catalog_template_similarity"]


# Create an approved development pilot with the synthetic review records required for scoring.
def scoring_pilot(tmp_path):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    write_jsonl(pilot / "selected_pages.jsonl", [page()])
    write_json(
        pilot / "pilot_manifest.json",
        {
            "split": "DEVELOPMENT",
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(pilot),
        },
    )
    return pilot


# Check that scoring preflight uses only bound metadata.
def test_scoring_preflight_uses_only_bound_metadata(tmp_path):
    pilot = scoring_pilot(tmp_path)
    assert module.guard_development_scoring(pilot)["windows"] == 1
    (pilot / "selected_pages.jsonl").write_text("corrupted")
    with pytest.raises(ValueError, match="checksum changed"):
        module.guard_development_scoring(pilot)


# Require an approved exposed evaluation before opening its sample metadata.
def test_registered_exposure_required_before_pilot_metadata_read(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("unregistered metadata read"))
    with pytest.raises(ValueError, match="registered"):
        module.load_exposed_split_metadata(
            tmp_path / "pilot", tmp_path / "evaluation", tmp_path / "out"
        )


# Check that manifest alias cannot escape before metadata read.
def test_manifest_alias_cannot_escape_before_metadata_read(tmp_path, monkeypatch):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    (pilot / "pilot_manifest.json").symlink_to(tmp_path / "outside.json")
    monkeypatch.setattr(module, "read_json", lambda *a: pytest.fail("escaped metadata read"))
    with pytest.raises(ValueError, match="escapes"):
        module.guard_development_scoring(pilot)


# Reject a reserved path before opening the requested file.
@pytest.mark.parametrize("entrypoint", ["audit", "scoring", "training", "pilot"])
def test_reserved_path_guard_precedes_input_read(tmp_path, monkeypatch, entrypoint):
    from course_parser import entry_learning, learning

    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )
    for target in (module, entry_learning, learning):
        monkeypatch.setattr(target, "read_json", lambda *a: pytest.fail("reserved input opened"))
    with pytest.raises(ValueError, match="reserved cohort access"):
        if entrypoint == "audit":
            module.audit_exposed_design(forbidden, tmp_path / "eval", tmp_path / "out")
        elif entrypoint == "scoring":
            module.guard_development_scoring(forbidden)
        elif entrypoint == "training":
            train_entry_ranker(forbidden, tmp_path / "out")
        else:
            learning.verify_learning_pilot(forbidden)


# Check that explicit exposure cannot be hidden by tuning tag.
def test_explicit_exposure_cannot_be_hidden_by_tuning_tag():
    rows = [
        {
            "learning_partition": "TUNING",
            "learning_group": "g",
            "label": i,
            "features": {"bias": 1.0},
            "evaluation_role": "EXPOSED_DIAGNOSTIC",
        }
        for i in (0, 1)
    ]
    with pytest.raises(ValueError, match="TUNING"):
        fit_ranker(rows)


# Verify the approved sample identity without reading source-review answers.
def test_approved_metadata_loader_verifies_pilot_identity_without_answers(tmp_path, monkeypatch):
    from course_parser import field_scoring

    pilot = scoring_pilot(tmp_path)
    evaluation = tmp_path / "evaluation"
    evaluation.mkdir()
    write_json(
        evaluation / "evaluation_manifest.json",
        {
            "pilot_manifest_sha256": sha256(pilot / "pilot_manifest.json"),
        },
    )
    monkeypatch.setattr(field_scoring, "enforce_policy", lambda *a: None)
    result = module.audit_exposed_design(pilot, evaluation, tmp_path / "out")
    assert result["windows"] == 1
    manifest = read_json(tmp_path / "out/manifest.json")
    assert not manifest["answers_or_source_text_opened"]
    assert not manifest["protected_evaluation_opened"]
    (pilot / "pilot_manifest.json").write_text("changed")
    with pytest.raises(ValueError, match="differs"):
        module.load_exposed_split_metadata(pilot, evaluation, tmp_path / "bad-out")


# Allow the diagnostic command-line alias to select the legacy partition only after the access
# checks pass.
def test_diagnostic_cli_alias_selects_legacy_partition_after_preflight(tmp_path, monkeypatch):
    from course_parser import cli

    calls = []
    monkeypatch.setattr(module, "guard_development_scoring", lambda *a: calls.append("guard"))
    monkeypatch.setattr(cli, "ReferenceProject", lambda *a: object())

    # Score a possible course boundary from independent source cues while preserving
    # continuation conflicts and review reasons.
    def score(*args, **kwargs):
        calls.append(kwargs["partitions"])
        return {"status": "FIXTURE_ONLY"}

    monkeypatch.setattr(cli, "score_learning", score)
    args = [
        "score-learning",
        "--reference-project",
        str(tmp_path),
        "--pilot",
        str(tmp_path),
        "--predictions",
        str(tmp_path),
        "--output",
        str(tmp_path / "out"),
        "--diagnostic",
    ]
    assert cli.main(args) == 0
    assert calls == ["guard", ("VALIDATION",)]
    calls.clear()
    assert cli.main(args + ["--partition", "TUNING"]) == 1
    assert calls == []


# Reject reserved command-line inputs before the scorer is called.
def test_scoring_cli_reserved_paths_rejected_before_scorer(tmp_path, monkeypatch):
    from course_parser import cli

    forbidden = Path(__file__).resolve().parents[1] / (
        "artifacts/validation-transfer-v1/fresh-validation-reservation-v3"
    )
    monkeypatch.setattr(cli, "score_learning", lambda *a, **k: pytest.fail("scorer called"))
    monkeypatch.setattr(cli, "ReferenceProject", lambda *a: pytest.fail("reference opened"))
    assert (
        cli.main(
            [
                "score-learning",
                "--reference-project",
                str(tmp_path),
                "--pilot",
                str(forbidden),
                "--predictions",
                str(tmp_path),
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 1
    )
