# Phase 14: institution family evaluation

The recommendation to rotate institution families is sound for a component that
can be trained afresh in each fold. This implementation runs that experiment for
the existing structural entry ranker using TUNING labels only. It also adds a
cached breakdown of complete-course performance by institution family. A breakdown
of a parser already developed using those institutions is not leave-one-out
retraining and does not establish unseen-institution generalization.

A low institution score does not prove that layout is the cause. Review source
quality, OCR, annotation completeness, candidate coverage and sample composition
before attributing a failure to a layout family. The percentages in the screenshot
are illustrative; this report uses the repository's actual approved cached scores.

## Inputs and isolation

`evaluate-institution-out` accepts only checksum-pinned evaluations registered in
`config/development_phase_execution_v1.json`. Before opening cached labels it
verifies the bound DEVELOPMENT pilot and its selected metadata. It opens only
selected metadata, evaluation manifests, cached page counts and cached entry
labels. No PDF, image, raw course-review answer file or reserved cohort is opened.
The 34-window cohort remains sealed until all 18 phases are complete and the
parser is frozen. No protected-cohort isolation or template-similarity audit is
performed during this phase.

The Phase 13 dependency graph supplies the fold families: stable institution IDs
and aliases, legacy learning groups, declared catalog families, shared PDF hashes,
and shared physical context pages are joined transitively. All connected windows
stay together. The canonical ID is the component's smallest learning-group ID;
it is deterministic for the bound input inventory and is not a model feature.
Unknown catalog-template similarities remain unknown. Diagnostic roles cannot
share a component with TUNING.

Each candidate must match its metadata group, partition, physical central page
and PDF identity. Duplicate candidate IDs, unknown windows, unreviewed-window
labels, role contradictions, missing review provenance, nonstructural features
and nonfinite feature values are rejected. Only inventory-aligned labels are
accepted; no independent adjudication is inferred from a single review.

## Experiment

For each TUNING family, fit new logistic weights on labeled candidates in the
other TUNING families. Each fitted vocabulary and class/family weight comes only
from that fold's training rows. The existing settings are fixed: 600 iterations,
learning rate 0.3, regularization 0.02, decision threshold 0.5. Weighting gives each
class equal weight and each connected family within that class equal weight.
No threshold search, hyperparameter selection, CRF training or LayoutLM training
is performed. Diagnostic candidates are excluded from every fold.

The output records training/test window IDs and candidate IDs, source membership,
class counts, input hashes, fitted weights and out-of-family predictions. A family
with no reviewed candidates is explicitly skipped. A fold with a missing training
class is skipped with a reason. All-negative test families remain valid false
positive controls; their positive recall is undefined.

Metrics are conditional candidate recognition, not complete-course extraction.
Unproposed headers are outside these candidate labels. Source inventories,
baseline decisions and structural features were developed on exposed material;
excluding a family from new weight fitting cannot undo that exposure. Baseline
comparisons are diagnostic, not an unbiased comparison with an independently
developed parser. Future model selection from these folds must also be reported
as development; untouched evaluation remains deferred.

## Results and decision

The expanded approved evaluation contains 68 windows in 16 connected families.
Twelve families have TUNING windows. Seven have reviewed candidate labels and
complete folds; five lack labels and are skipped. The experiment evaluates all
702 TUNING candidates once, with 95 positives and 607 negatives. All 176 legacy
VALIDATION candidate labels are excluded. Zero TUNING labels carry independent
adjudication metadata.

| Conditional candidate result | Existing decisions | Fresh family-excluded ranker |
| --- | ---: | ---: |
| True positives | 78 | 82 |
| False positives | 0 | 131 |
| False negatives | 17 | 13 |
| Precision | 100.0% | 38.5% |
| Recall | 82.1% | 86.3% |
| Pooled F1 | 90.2% | 53.2% |

The ranker remains experimental and is not activated in extraction. The modest
candidate-recall gain does not justify the added false positives. No tuning is
performed using these results. Hartnell contributes 108 ranker false positives;
Mines contributes 23 on an all-negative reviewed candidate set. These are
candidate label counts, not newly emitted courses or claims about reserved data.

Parser family counts reconcile exactly with the cached strict evaluation:
66/128 complete core courses, 13/97 complete all-field courses, zero measured
false course proposals, five ambiguous course proposals, one description
contamination, zero parser failures. Fifty windows are scored and 18 await source
review. This phase changes evaluation tooling, not extraction or strict scoring.

Both pooled and equally weighted family averages are exported with denominators.
Undefined rates remain null. Since baseline F1 is undefined on the all-negative
family while the ranker incurs false positives, the ordinary macro F1 averages
have different defined-family counts (six versus seven). A paired macro also
compares the six common families; pooled confusion counts include every evaluated
candidate. No uncertainty interval treating correlated windows or candidates as
independent observations is invented.

## Reproduction

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser evaluate-institution-out \
  --pilot artifacts/table-ownership-v1/expanded-pilot \
  --evaluation artifacts/ocr-recovery-phase10-v1/candidate-v2/expanded-evaluation \
  --output artifacts/institution-out-phase14-v1/new-run
```

The output directory must be new. `institution_out.json` contains full family
counts, scope notes, fold weights and metrics; the JSONL contains held-out
candidate predictions. The manifest binds the exact permitted input files and
implementation hashes. Tests exercise transitive family grouping, label and
feature isolation, missing classes, unreviewed families, undefined denominators,
strict-count reconciliation, exposure validation and pre-read path guards.

The official [LeaveOneGroupOut documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.LeaveOneGroupOut.html)
defines training on all groups except one. The
[cross-validation guide](https://scikit-learn.org/stable/modules/cross_validation.html)
explains why tuning with evaluation feedback compromises generalization claims.
Those principles motivate the scoped experiment and its exposure limits.
