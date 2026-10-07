# Prepare permitted text, geometry and image inputs for the future field-classifier
# comparison. Report what is ready and what remains deferred without running a model or
# evaluator.
"""Prepare permitted Phase 12 inputs and report deferral; no model or evaluator is run."""

from __future__ import annotations

import argparse
from pathlib import Path

from course_parser.field_experiment import prepare_field_experiment
from course_parser.field_scoring import enforce_policy
from course_parser.io import artifact_hashes, read_json, sha256, verify_files, write_json
from course_parser.omission_review import _reject_reserved_paths

WORKSPACE = Path(__file__).resolve().parents[1]
PHASE11 = WORKSPACE / "artifacts/crf-readiness-phase11-v1/release-v4"
BASELINE = WORKSPACE / "artifacts/ocr-recovery-phase10-v1/candidate-v2"


# Read the command-line options, then prepare permitted text, geometry and image inputs for
# the future field-classifier comparison.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _reject_reserved_paths(args.output)
    for name in ("original", "expanded"):
        enforce_policy(BASELINE / (name + "-evaluation"), args.output)
    args.output.mkdir(parents=True, exist_ok=False)
    result = prepare_field_experiment(
        PHASE11 / "tuning-templates",
        PHASE11 / "first_batch_template.jsonl",
        args.output / "shared-inputs",
    )
    verify_files(
        args.output / "shared-inputs",
        read_json(args.output / "shared-inputs/manifest.json")["file_sha256"],
    )
    baselines = {}
    for name in ("original", "expanded"):
        path = BASELINE / (name + "-evaluation/evaluation_manifest.json")
        value = read_json(path)
        baselines[name] = {"overall": value["overall"], "manifest_sha256": sha256(path)}
    outcomes = {
        "status": "PHASE12_PREPARATION_IMPLEMENTED_LAYOUTLMV3_EXPERIMENT_DEFERRED",
        "source_preparation": result,
        "baseline_metrics_reused_not_rescored": baselines,
        "extraction_accuracy_gain_claimed": False,
        "models_downloaded_installed_trained_or_run": False,
        "protected_evaluation_opened": False,
        "strict_scorer_sha256": sha256(WORKSPACE / "src/course_parser/learning_evaluation.py"),
        "limitations": [
            "No reviewed word-label dataset or permitted independent field-validation split.",
            "Prepared geometry is segment-level, repeated for words, not precise word boxes.",
            "726 units have unavailable typography; no typography was guessed.",
            "Source word offsets are prepared; actual subword/overflow alignment is deferred "
            "until a pinned tokenizer exists. No tokenizer has been downloaded or executed.",
            "Paired-output audit checks declared hashes/coverage only, not whether training "
            "or shared downstream assembly actually ran as declared.",
            "Image/no-image comparison tests the visual branch. A constant-image control "
            "would further distinguish image content from extra visual tokens/compute.",
        ],
        "sources": [
            "https://github.com/microsoft/unilm/tree/master/layoutlmv3",
            "https://github.com/microsoft/unilm/blob/master/layoutlmv3/examples/run_funsd_cord.py",
            "https://huggingface.co/docs/transformers/model_doc/layoutlmv3",
        ],
    }
    write_json(args.output / "outcomes.json", outcomes)
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Phase 12: LayoutLMv3 readiness</title><style>
body{{font:17px/1.6 system-ui;max-width:940px;margin:40px auto;padding:0 24px;color:#243247}}
h1{{line-height:1.15}}a{{color:#17549b}}.note{{padding:16px;background:#eef3fa}}
table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;
border-bottom:1px solid #ccd5df}}</style>
<h1>Phase 12: prepare a narrow field experiment</h1>
<p class="note">Preparation implemented. LayoutLMv3 training and inference deferred.
Reserved 34-window cohort remains sealed until all 18 phases are complete and the parser is
frozen.</p>
<h2>Assessment</h2><p>The recommendation is appropriate: test field-role suggestions while retaining
source text, reading order, ownership, boundaries and course assembly. Existing course-level
reviews do not supply word-level gold labels. There is no demonstrated catalog-specific gain from
LayoutLMv3, and no reviewed evidence that images solve a dominant remaining failure class.</p>
<p>The proposed simple classifier versus LayoutLMv3 comparison tests entire systems; their capacity
and pretraining differ, so it cannot attribute a gain solely to images. Our contract adds an
image-disabled
LayoutLMv3 arm. A constant-image control would further isolate image content from extra visual
tokens
and compute. The image benefit remains unmeasured.</p>
<h2>Implemented shared input and comparison contract</h2>
<table><tr><th>Prospective arm</th><th>Input features</th><th>Current status</th></tr>
<tr><td>Simple field classifier</td><td>Text + relative geometry + native emphasis</td>
<td>Inputs prepared; classifier training deferred</td></tr>
<tr><td>LayoutLMv3 without images</td><td>Same text/boxes; visual embeddings disabled</td>
<td>Contract prepared; model not run</td></tr>
<tr><td>LayoutLMv3 with images</td><td>Same text/boxes + bound page image</td>
<td>Contract prepared; model not run</td></tr></table>
<p>The future comparison requires shared audited labels, institution-grouped folds, source word
windows and downstream
assembly. Role predictions may propose TITLE, DESCRIPTION, PREREQUISITES and NONCOURSE;
CODE, CREDITS and other contexts remain distinct in the full label inventory. The screenshot's
NON_COURSE_REFERENCE maps only to our existing broader NONCOURSE class; no finer reference labels
are invented. No proposed field can create a course or assign an owner automatically.</p>
<h2>Verified preparation</h2><ul><li>{result["windows"]} TUNING windows;
{result["page_frames"]} source page images; {result["source_units"]:,} original source units;
{result["feature_counts"]["words"]:,} exact source words.</li>
<li>All 2,270 units have canonical source boxes. 1,544 have exact native typography;
726 explicitly lack it.</li><li>Original case, punctuation, hyphens, OCR errors and character
offsets
are preserved. Segment boxes are repeated for words and clearly labeled as segment geometry.</li>
<li>Page-image hashes and canonical-to-image/normalized-box transforms are recorded. No PDF or OCR
text is replaced. Zero adjudicated windows, zero model targets supplied in this batch.</li></ul>
<h2>Safeguards and limits</h2><p>New commands block reserved paths, escapes and aliases before reads
or writes. Word labels can be projected only from independently audited human spans; pending or
source-insufficient windows remain unlabeled. A field boundary inside a source word stays
unresolved.
Human span splits cannot change the common source words.</p>
<p>The shadow-output audit rejects changed text, duplicate or missing word spans, unexpected labels,
different input/annotation/fold/budget/seed/window hashes, different downstream assembly
declarations,
fresh OCR and mismatched image/no-image checkpoint declarations. Every missing prediction must be
an explicit abstention. This checks provenance declarations and coverage, not actual training
behavior
or accuracy. No learned field proposal is connected to extraction acceptance.</p>
<p>Tokenizer/subword packing is deferred. The future runner must track word_ids and overflow
mapping,
keep every overflow window, handle special/repeated subwords consistently, and avoid treating 512
subwords as 512 words. One page image belongs to each model sequence; course continuation remains
in the source-constrained assembly stage.</p>
<h2>Relevant official code and documentation</h2><p>Microsoft's
<a href="https://github.com/microsoft/unilm/blob/master/layoutlmv3/examples/run_funsd_cord.py">
fine-tuning example</a> separates token/label alignment, overflow windows, optional image inputs and
training/evaluation. We adapt those interfaces without importing its form-specific labels or
trainer.</p>
<p>The <a href="https://huggingface.co/docs/transformers/model_doc/layoutlmv3">official Transformers
documentation</a> supports supplied text/boxes with apply_ocr=False, normalized 0–1000 boxes and
a visual_embed switch. These support the interface; they do not establish course-parser
accuracy.</p>
<h2>What must happen before training</h2><p>Adjudicate the source spans and order; verify word-level
alignment; establish a permitted institution-grouped field-validation split with catalog families
and overlapping context pages kept together; document residual image-dependent errors. Use no
reserved
cohort material. Pin tokenizer/model revision, labels, splits, seeds, budgets and chunk mapping.
Then compare paired field recognition and complete-course assembly, false ownership, contamination,
boundaries, abstentions and resource costs. Exposed diagnostics cannot establish untouched
generalization.
Keep the simpler approach unless measured improvement justifies the additional model.</p>
<h2>Course accuracy</h2><p>No new parser run or rescore. Extraction code and strict scorer are
unchanged.
Accepted Phase 10 results are reused: original 35/52 core and 10/38 all fields; expanded 66/128 core
and 13/97 all fields, zero measured false courses in both. Expanded includes original.
This phase claims preparation and safeguards, not extraction improvement.</p>
<p><a href="shared-inputs/experiment_contract.json">Experiment contract</a> ·
<a href="shared-inputs/common_source_inputs.jsonl">Common source inputs</a> ·
<a href="shared-inputs/page_frames.jsonl">Image frames and transforms</a> ·
<a href="shared-inputs/annotation_readiness.json">Annotation readiness</a> ·
<a href="outcomes.json">Evidence and baseline hashes</a></p></html>"""
    (args.output / "index.html").write_text(html)
    write_json(
        args.output / "manifest.json",
        {
            "status": outcomes["status"],
            "protected_evaluation_opened": False,
            "file_sha256": artifact_hashes(args.output),
        },
    )
    print(result)


if __name__ == "__main__":
    main()
