# Phase 12: source-bound field experiment preparation

The recommendation is appropriate as a future narrow experiment. No evidence yet
establishes a LayoutLMv3 gain on our course catalogs. This phase implements input
preparation and a shadow-output provenance audit; model training, inference,
tokenizer packing and accuracy comparisons remain deferred.

The reserved 34-window institution-held-out cohort and its answers remain sealed
until all 18 phases are complete and the parser is frozen. Preparation accepts
only source-bound TUNING windows from the frozen DEVELOPMENT annotation pilot.

The current batch has six windows, 18 page frames, 2,270 source units and 11,249
source words. Zero windows have adjudicated labels. Geometry comes from canonical
source segment boxes, repeated for words; it is not precise word geometry.
Typography requires an exact native line match; 726 units have unavailable
typography. Images, native geometry and source references are verified against
the frozen pilot hashes. Source case, punctuation, offsets and order are retained.

The prospective comparison has three arms:

- A simple classifier using text, relative geometry and native emphasis.
- LayoutLMv3 with visual embeddings disabled.
- LayoutLMv3 with bound page images.

Comparing only the first and third arms cannot isolate image benefit because
their architectures and pretraining differ. The ablation tests the visual branch;
a future constant-image control would further distinguish image content from
extra visual tokens and compute. All arms must share audited labels, permitted
institution-grouped folds, word windows and downstream assembly. Provenance IDs
and institution names are excluded from the allowed model features.

TITLE, DESCRIPTION, PREREQUISITES and the existing broader NONCOURSE class are the
focus roles. The full field/context inventory remains distinct. Human span splits
cannot change common input words. Labels crossing a word boundary remain
unresolved. Future tokenization must preserve word/subword and overflow mappings,
use supplied native words with `apply_ocr=False`, and associate each sequence with
one page image. No 512-word chunking assumption is made.

Run from the workspace with `PYTHONPATH=src`:

```sh
python -m course_parser prepare-field-experiment \
  --templates <frozen-tuning-templates> --selected <audited-selection.jsonl> \
  --output <new-directory>
python -m course_parser audit-field-proposals \
  --experiment <prepared-directory> --proposals <paired-proposals.json> \
  --output <new-audit.json>
python scripts/prepare_phase12_field_report.py --output <new-report-directory>
```

The audit rejects missing/duplicate words, changed text, unsupported roles and
incompatible comparison declarations. Explicit abstentions preserve coverage.
It validates declared provenance and coverage, not authentic training execution,
accuracy or adoption. No proposal changes ownership, boundaries, source text or
course acceptance. The strict scorer is unchanged; existing Phase 10 scores are
reused without rescoring.

Actual experimentation requires reviewed word targets, an independent permitted
institution-grouped field-validation split and source-reviewed residual failures
that motivate image features. Neither labels nor evaluation may come from the
sealed cohort. Future adoption requires paired field and strict course results,
false-proposal/ownership checks and runtime evidence.

The [official LayoutLMv3 example](https://github.com/microsoft/unilm/blob/master/layoutlmv3/examples/run_funsd_cord.py)
and [Transformers documentation](https://huggingface.co/docs/transformers/model_doc/layoutlmv3)
support the supplied-word, segment-layout and visual-branch contracts. They do
not establish effectiveness on our catalogs.
