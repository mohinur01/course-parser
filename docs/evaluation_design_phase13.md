# Phase 13: exposed diagnostics and evaluation isolation

The recommendation is appropriate. Institution-grouped splits were already in
place, and the original 15-course VALIDATION set had already been reclassified as
exposed diagnostic material. This phase strengthens verification and reporting;
it does not alter parser decisions, scoring formulas or historical assignments.

The active [phase policy](../config/development_phase_execution_v1.json) keeps the
reserved 34-window cohort sealed until **all 18 phases are complete and the parser
is frozen**. No reservation file, source page, identity or answer was inspected.
The existing reservation serves the future fresh-validation role. No replacement
was selected. Separate protected TEST/calibration pools remain excluded.

The [evaluation contract](../config/evaluation_design_v1.json) defines four roles:

| Role | Permitted purpose |
| --- | --- |
| TUNING | Rule development and model fitting from permitted source material |
| EXPOSED_DIAGNOSTIC | Repeated diagnosis and rule development; no untouched-generalization claim |
| FRESH_VALIDATION | Existing sealed reservation; future confirmation after the full gate |
| TEST | Separate protected release evaluation under its own protocol |

Historical `VALIDATION` is displayed as `EXPOSED_DIAGNOSTIC` in new audits and
ranker outputs. The old registry and immutable reports retain their original
tags. TUNING-only ranker fitting remains enforced; contradictory exposure tags
are rejected. No ranker was fitted this phase.

Metadata checks take the transitive union of stable institution IDs, declared
aliases, learning groups, declared catalog families, PDF checksums and overlapping
physical context pages. A connected component cannot cross evaluation roles.
Catalog years of the same institution stay together. Missing identities, duplicate
central physical pages and invalid context windows fail verification. These
checks run in the learning-pilot verifier before source-page processing.

The new audit accepts only checksum-pinned, registered exposed evaluations and
their bound pilot metadata. It reads selected-page identities, not PDFs, images,
text, predictions or review answers. The scoring CLI checks development metadata
and reserved paths before answer import. Low-level historical scoring functions
retain their existing interface and formulas; phase scripts must continue using
approved, bound inputs. This is an additional workflow control, not an operating
system access restriction or proof that arbitrary copied files are permitted.

With `PYTHONPATH=src`, run:

```sh
python -m course_parser audit-evaluation-design \
  --pilot artifacts/source-learning-v4/pilot \
  --evaluation artifacts/ocr-recovery-phase10-v1/candidate-v2/original-evaluation \
  --output <new-audit-directory>
python scripts/build_phase13_evaluation_report.py --output <new-report-directory>
```

The scoring CLI's `--diagnostic` selects the legacy `VALIDATION` partition. It is
an alternative to `--partition`; it does not change strict scoring or relabel old
artifacts. Answer import remains a separate operation from this metadata audit.

The original audit covers 28 selected windows, 14 connected families and 84 unique
context pages. Expanded covers 68 windows, 16 families and 203 context pages, with
one overlapping window pair. Original is wholly included in expanded; summing
them would count 28 windows twice. Expanded retains 25 random selection windows.
Metadata windows include pending reviews and differ from reviewed-course counts.
No new course evaluation was run; accepted scores remain 35/52 and 66/128 strict
core completions on the two overlapping exposed samples.

No catalog-template family IDs are supplied in these pilots. The code enforces
declared links, but stable institution identities do not prove template
independence. Unknown cross-institution template relationships need permitted
source review before a template-transfer claim. Isolation against any sealed
cohort has **not** been verified here.

Future confirmation must pin parser/rules/models, dependencies, splits, sampling
and metric definitions; seal predictions independently of answers; preserve the
strict core metric and field diagnostics; and report false proposals, ownership,
contamination, source uncertainty, abstentions and cost. Use unique central-page
course starts for denominators. Report per-institution results and family-level
uncertainty; windows and catalog years are correlated observations. Outcomes used
to choose another change become exposed evidence. A renewed confirmation claim
then requires another untouched cohort. No access or freeze certificate is issued
by Phase 13.

This follows [grouped evaluation guidance](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data)
and [data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
The grouping keys are our catalog-specific policy. No new dependency is needed.
