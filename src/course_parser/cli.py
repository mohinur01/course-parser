# Define the commands available to project users and connect each command to its
# implementation. Start here to see how to prepare inputs, run the parser or check results.
"""Command-line entry points for the first development milestone."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bridge import INCOMPLETE, prepare_bridge, verify_run
from .entry_learning import build_review_queue, compare_learning_runs, train_entry_ranker
from .io import write_json
from .layout_stage import prepare_layout, verify_layout
from .learning import prepare_learning_pilot, run_learning_baseline, verify_learning_pilot
from .learning_evaluation import score_learning
from .ppstructure_runner import run_ppstructure
from .reference import ReferenceProject
from .release import TaskRelease


# Define the available commands and the arguments each command accepts.
def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Prepare traceable catalog review evidence.")
    commands = root.add_subparsers(dest="command", required=True)
    for name, description in (
        ("audit-release", "Validate the full frozen task inventory without opening source PDFs."),
        ("build-batch", "Render a bounded batch and connect it to v3 page evidence."),
        ("bridge-batch", "Connect an existing rendered batch to v3 page evidence."),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("--reference-project", type=Path, required=True)
        command.add_argument("--task-release", type=Path, required=True)
        command.add_argument("--vocabulary", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name != "audit-release":
            command.add_argument("--map-remote-root", type=Path)
            command.add_argument("--map-local-root", type=Path)
        if name == "bridge-batch":
            command.add_argument("--packet-dir", type=Path, required=True)
        elif name == "build-batch":
            command.add_argument("--start-rank", type=int, required=True)
            command.add_argument("--count", type=int, required=True)
    verify = commands.add_parser("verify-run", help="Check an exported evidence bundle.")
    verify.add_argument("--run", type=Path, required=True)
    verify.add_argument("--recheck-inputs", action="store_true")
    layout = commands.add_parser(
        "enrich-layout", help="Add PDF cells and optional Docling/PP-Structure proposals."
    )
    layout.add_argument("--reference-project", type=Path, required=True)
    layout.add_argument("--evidence-run", type=Path, required=True)
    layout.add_argument("--docling-manifest", type=Path)
    layout.add_argument("--ppstructure-manifest", type=Path)
    layout.add_argument("--output", type=Path, required=True)
    layout_verify = commands.add_parser(
        "verify-layout", help="Check an exported page-layout bundle."
    )
    layout_verify.add_argument("--run", type=Path, required=True)
    layout_verify.add_argument("--recheck-inputs", action="store_true")
    pp_run = commands.add_parser(
        "run-ppstructure", help="Run optional PP-Structure models on verified page images."
    )
    pp_run.add_argument("--evidence-run", type=Path, required=True)
    pp_run.add_argument("--output", type=Path, required=True)
    pp_run.add_argument("--device", default="cpu")
    pp_run.add_argument("--limit", type=int)
    learning = commands.add_parser(
        "prepare-learning-pilot", help="Prepare real DEVELOPMENT learning windows."
    )
    learning.add_argument("--reference-project", type=Path, required=True)
    learning.add_argument("--output", type=Path, required=True)
    learning.add_argument("--partition-registry", type=Path)
    learning.add_argument("--expand-review", action="store_true")
    expanded = commands.add_parser(
        "build-expanded-review", help="Retain random pages and add source-only review signals."
    )
    for name in ("pilot", "predictions", "output"):
        expanded.add_argument("--" + name, type=Path, required=True)
    baseline = commands.add_parser(
        "run-learning-baseline", help="Propose courses before opening answer keys."
    )
    baseline.add_argument("--reference-project", type=Path, required=True)
    baseline.add_argument("--pilot", type=Path, required=True)
    baseline.add_argument("--output", type=Path, required=True)
    baseline.add_argument("--repair-prose-columns", action="store_true")
    baseline.add_argument("--contextual-course-assembly", action="store_true")
    baseline.add_argument(
        "--ordering-engine",
        choices=("legacy", "source_rows", "docling", "ppstructure"),
        default="source_rows",
    )
    baseline.add_argument("--engine-exports", type=Path)
    sequence = commands.add_parser(
        "export-sequence-templates",
        help="Prepare blank TUNING source-span annotations for future CRF experiments.",
    )
    sequence.add_argument("--reference-project", type=Path, required=True)
    sequence.add_argument("--pilot", type=Path, required=True)
    sequence.add_argument("--predictions", type=Path, required=True)
    sequence.add_argument("--output", type=Path, required=True)
    sequence_audit = commands.add_parser(
        "audit-sequence-reviews", help="Check source-span labels and CRF readiness."
    )
    sequence_audit.add_argument("--templates", type=Path, required=True)
    sequence_audit.add_argument("--reviewed", type=Path, required=True)
    sequence_audit.add_argument("--output", type=Path, required=True)
    field_prepare = commands.add_parser(
        "prepare-field-experiment",
        help="Prepare source-only inputs for deferred field experiments.",
    )
    field_prepare.add_argument("--templates", type=Path, required=True)
    field_prepare.add_argument("--selected", type=Path, required=True)
    field_prepare.add_argument("--output", type=Path, required=True)
    field_audit = commands.add_parser(
        "audit-field-proposals",
        help="Check three-arm shadow provenance without scoring or adoption.",
    )
    field_audit.add_argument("--experiment", type=Path, required=True)
    field_audit.add_argument("--proposals", type=Path, required=True)
    field_audit.add_argument("--output", type=Path, required=True)
    design = commands.add_parser(
        "audit-evaluation-design", help="Audit approved exposed split metadata without answers."
    )
    design.add_argument("--pilot", type=Path, required=True)
    design.add_argument("--evaluation", type=Path, required=True)
    design.add_argument("--output", type=Path, required=True)
    accounting = commands.add_parser(
        "audit-page-accounting", help="Audit retained native rows in approved exposed caches."
    )
    accounting.add_argument("--reference-project", type=Path, required=True)
    accounting.add_argument("--pilot", type=Path, required=True)
    accounting.add_argument("--evaluation", type=Path, required=True)
    accounting.add_argument("--output", type=Path, required=True)
    precision = commands.add_parser(
        "audit-precision",
        help="Route approved exposed proposals and measure fixed header coverage.",
    )
    precision.add_argument("--reference-project", type=Path, required=True)
    precision.add_argument("--pilot", type=Path, required=True)
    precision.add_argument("--evaluation", type=Path, required=True)
    precision.add_argument("--output", type=Path, required=True)
    institution_out = commands.add_parser(
        "evaluate-institution-out", help="Run exposed family diagnostics and TUNING ranker folds."
    )
    institution_out.add_argument("--pilot", type=Path, required=True)
    institution_out.add_argument("--evaluation", type=Path, required=True)
    institution_out.add_argument("--output", type=Path, required=True)
    annotation_batch = commands.add_parser(
        "prepare-annotation-batch", help="Select a mixed source-bound TUNING annotation batch."
    )
    annotation_batch.add_argument("--pilot", type=Path, required=True)
    annotation_batch.add_argument("--evaluation", type=Path, required=True)
    annotation_batch.add_argument("--templates", type=Path, required=True)
    annotation_batch.add_argument("--reviewed", type=Path)
    annotation_batch.add_argument("--output", type=Path, required=True)
    annotation_batch.add_argument("--budget", type=int, default=10)
    annotation_batch.add_argument("--seed", type=int, default=1501)
    annotation_batch.add_argument(
        "--shares", nargs=3, type=int, default=(40, 30, 30), metavar=("FAILURE", "STYLE", "RANDOM")
    )
    annotation_batch.add_argument("--family-cap", type=int, default=2)
    learning_verify = commands.add_parser(
        "verify-learning-pilot", help="Verify learning source evidence and splits."
    )
    learning_verify.add_argument("--run", type=Path, required=True)
    score = commands.add_parser(
        "score-learning", help="Compare sealed proposals with source reviews."
    )
    score.add_argument("--reference-project", type=Path, required=True)
    score.add_argument("--pilot", type=Path, required=True)
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--page-reviews", type=Path)
    score.add_argument("--course-truth", type=Path)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--partition", choices=("TUNING", "VALIDATION"), action="append")
    score.add_argument(
        "--diagnostic", action="store_true", help="Score legacy VALIDATION as exposed diagnostics."
    )
    train = commands.add_parser(
        "train-entry-ranker", help="Fit a structural review aid on TUNING only."
    )
    train.add_argument("--evaluation", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser(
        "compare-learning", help="Compare parser versions on the same reviewed pages."
    )
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    queue = commands.add_parser(
        "build-learning-review-queue", help="Prioritize source windows for the next review."
    )
    queue.add_argument("--evaluation", type=Path, required=True)
    queue.add_argument("--model-run", type=Path, required=True)
    queue.add_argument("--output", type=Path, required=True)
    omissions = commands.add_parser(
        "build-omission-review", help="Audit source signals and preserve separate crop extractions."
    )
    omissions.add_argument("--pilot", type=Path, required=True)
    omissions.add_argument("--predictions", type=Path, required=True)
    omissions.add_argument("--output", type=Path, required=True)
    omissions.add_argument("--crop-limit", type=int, default=12)
    omissions.add_argument("--dpi", type=int, default=300)
    omissions.add_argument("--tesseract", default="tesseract")
    omissions.add_argument("--prepare-only", action="store_true")
    crop_pp = commands.add_parser(
        "run-crop-ppstructure", help="Add optional independent OCR to prepared crops."
    )
    crop_pp.add_argument("--run", type=Path, required=True)
    crop_pp.add_argument("--limit", type=int)
    crop_pp.add_argument("--device", default="cpu")
    for name in ("finalize-omission-review", "verify-omission-review"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
    return root


# Read the command-line arguments and run the requested project command.
def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "build-omission-review":
            from .omission_review import finalize_omission_review, prepare_omission_review

            result = prepare_omission_review(
                args.pilot,
                args.predictions,
                args.output,
                crop_limit=args.crop_limit,
                dpi=args.dpi,
                tesseract=args.tesseract,
            )
            if not args.prepare_only:
                result = finalize_omission_review(args.output)
        elif args.command == "run-crop-ppstructure":
            from .crop_ppstructure import run_crop_ppstructure

            result = run_crop_ppstructure(args.run, limit=args.limit, device=args.device)
        elif args.command in ("finalize-omission-review", "verify-omission-review"):
            from .omission_review import finalize_omission_review, verify_omission_review

            result = (
                finalize_omission_review
                if args.command == "finalize-omission-review"
                else verify_omission_review
            )(args.run)
        elif args.command == "export-sequence-templates":
            from .sequence_annotations import export_sequence_templates

            ReferenceProject(args.reference_project)
            result = export_sequence_templates(args.pilot, args.predictions, args.output)
        elif args.command == "audit-sequence-reviews":
            from .omission_review import _reject_reserved_paths
            from .sequence_annotations import audit_sequence_reviews

            _reject_reserved_paths(args.templates, args.reviewed, args.output)
            result = audit_sequence_reviews(args.templates, args.reviewed)
            write_json(args.output, result)
        elif args.command == "prepare-field-experiment":
            from .field_experiment import prepare_field_experiment

            result = prepare_field_experiment(args.templates, args.selected, args.output)
        elif args.command == "audit-field-proposals":
            from .field_experiment import audit_field_proposals
            from .omission_review import _reject_reserved_paths

            _reject_reserved_paths(args.experiment, args.proposals, args.output)
            result = audit_field_proposals(args.experiment, args.proposals)
            write_json(args.output, result)
        elif args.command == "audit-evaluation-design":
            from .evaluation_design import audit_exposed_design

            result = audit_exposed_design(args.pilot, args.evaluation, args.output)
        elif args.command == "evaluate-institution-out":
            from .institution_out import evaluate_institution_out

            result = evaluate_institution_out(args.pilot, args.evaluation, args.output)
        elif args.command == "audit-page-accounting":
            from .page_accounting_audit import audit_cached_pages

            result = audit_cached_pages(
                ReferenceProject(args.reference_project), args.pilot, args.evaluation, args.output
            )
        elif args.command == "audit-precision":
            from .precision_audit import audit_precision

            result = audit_precision(
                ReferenceProject(args.reference_project), args.pilot, args.evaluation, args.output
            )
        elif args.command == "prepare-annotation-batch":
            from .active_annotations import prepare_annotation_batch

            result = prepare_annotation_batch(
                args.pilot,
                args.evaluation,
                args.templates,
                args.output,
                reviewed=args.reviewed,
                budget=args.budget,
                seed=args.seed,
                shares=tuple(args.shares),
                family_cap=args.family_cap,
            )
        elif args.command == "build-learning-review-queue":
            result = build_review_queue(args.evaluation, args.model_run, args.output)
        elif args.command == "train-entry-ranker":
            result = train_entry_ranker(args.evaluation, args.output)
        elif args.command == "compare-learning":
            result = compare_learning_runs(args.before, args.after, args.output)
        elif args.command == "score-learning":
            from .evaluation_design import guard_development_scoring

            # Both review and course-truth files are required.
            if (args.page_reviews is None) != (args.course_truth is None):
                raise ValueError("both review and course-truth files are required")
            # Choose one partition-selection option so the requested evaluation role is
            # unambiguous.
            if args.diagnostic and args.partition:
                raise ValueError("use --diagnostic or --partition, not both")
            guard_development_scoring(
                args.pilot,
                args.predictions,
                args.output,
                *([args.page_reviews, args.course_truth] if args.page_reviews else []),
            )
            result = score_learning(
                ReferenceProject(args.reference_project),
                args.pilot,
                args.predictions,
                args.output,
                review_path=args.page_reviews,
                truth_path=args.course_truth,
                partitions=("VALIDATION",)
                if args.diagnostic
                else tuple(args.partition)
                if args.partition
                else None,
            )
        elif args.command == "prepare-learning-pilot":
            result = prepare_learning_pilot(
                ReferenceProject(args.reference_project),
                args.output,
                partition_registry=args.partition_registry,
                expand_review=args.expand_review,
            )
        elif args.command == "build-expanded-review":
            from .expanded_review import build_expanded_review

            result = build_expanded_review(args.pilot, args.predictions, args.output)
        elif args.command == "run-learning-baseline":
            result = run_learning_baseline(
                ReferenceProject(args.reference_project),
                args.pilot,
                args.output,
                repair_prose_columns=args.repair_prose_columns,
                contextual_course_assembly=args.contextual_course_assembly,
                ordering_engine=args.ordering_engine
                if args.contextual_course_assembly
                else "legacy",
                engine_exports=args.engine_exports,
            )
        elif args.command == "verify-learning-pilot":
            result = verify_learning_pilot(args.run)
        elif args.command == "run-ppstructure":
            result = run_ppstructure(
                args.evidence_run, args.output, device=args.device, limit=args.limit
            )
        elif args.command == "verify-run":
            result = verify_run(args.run, recheck_inputs=args.recheck_inputs)
        elif args.command == "verify-layout":
            result = verify_layout(args.run, recheck_inputs=args.recheck_inputs)
        elif args.command == "enrich-layout":
            result = prepare_layout(
                ReferenceProject(args.reference_project),
                args.evidence_run,
                args.output,
                docling_manifest=args.docling_manifest,
                ppstructure_manifest=args.ppstructure_manifest,
            )
        else:
            # Both source mapping roots are required.
            if args.command != "audit-release" and (
                (args.map_remote_root is None) != (args.map_local_root is None)
            ):
                raise ValueError("both source mapping roots are required")
            reference = ReferenceProject(args.reference_project)
            release = TaskRelease.load(args.task_release, args.vocabulary, reference)
            if args.command == "audit-release":
                result = release.audit()
                args.output.mkdir(parents=True, exist_ok=False)
                write_json(args.output / "release_audit.json", result)
            elif args.command == "bridge-batch":
                result = prepare_bridge(
                    reference,
                    release,
                    args.packet_dir,
                    args.output,
                    remote_root=args.map_remote_root,
                    local_root=args.map_local_root,
                )
            else:
                release.select(args.start_rank, args.count)
                args.output.mkdir(parents=True, exist_ok=False)
                reference.materialize(
                    release.root,
                    args.output / "source_packet",
                    start_rank=args.start_rank,
                    count=args.count,
                    remote_root=args.map_remote_root,
                    local_root=args.map_local_root,
                )
                result = prepare_bridge(
                    reference,
                    release,
                    args.output / "source_packet",
                    args.output / "evidence",
                    remote_root=args.map_remote_root,
                    local_root=args.map_local_root,
                )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return (
            2
            if result.get("status") in {INCOMPLETE, "LAYOUT_INPUT_INCOMPLETE", "PROPOSALS_PARTIAL"}
            else 0
        )
    # Handle oserror, value error, key error, runtime error by keeping the failure explicit or
    # using the documented fallback.
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(
            json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr
        )
        return 1
