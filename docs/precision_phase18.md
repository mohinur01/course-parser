# Phase 18: precision and proposal review

The precision advice is appropriate. Its claim of zero known false courses is a
statement about reviewed header alignments, not a guarantee of correct titles,
descriptions, boundaries, optional fields or unseen institutions. A parser can
recognize a real course while attaching the wrong description.

Version 0.24.0 adds a mandatory source-only routing policy to learning predictions.
The policy preserves the current detector, independent-entry admission, source
rows, ownership, boundaries, continuation and field text. It does not use numeric
confidence, keywords, institution names, expected answers or a trained ranker.
No accuracy or recall improvement is claimed.

## Policy

| Band | Evidence and action |
| --- | --- |
| HIGH | An existing independently admitted record has a supported header, confirmed course context, no contradictory evidence, confirmed boundaries, resolved continuation and supported code/title/description. Source-confirmed description absence is allowed. Existing parser warnings, unresolved block evidence and ambiguous optional metadata prevent HIGH. The record is eligible for automatic **proposal**, and still requires human publication review. |
| MEDIUM | An existing record has missing or unresolved core evidence; retain it with explicit review reasons. An independently admitted entry without a recovered record raises a source-review question and creates no record. |
| LOW | Independent-course admission is unresolved; raise an omission question, with no new course, field ownership or publication. |
| EXCLUDED | An explicit reference or noncourse decision remains in its separate audit. Do not flood the omission queue with known rejected references. |

Bands are uncalibrated evidence categories. HIGH describes core proposal readiness,
not complete-record correctness. Missing credits/prerequisites remain explicit
metadata-review reasons; missing printed credits alone cannot reject a genuine
creditless course. Partial, ambiguous and withheld optional fields require MEDIUM.
All routes have `human_review_required=true`, `publication_authorized=false` and
blank review decisions. No authoritative human answer or keyword ledger is updated.

Native whole-page accounting runs first. Routing rejects mismatched source/scope,
missing decisions, duplicate candidate/record identities, conflicting record/block
identities and automatic publication. Its independent audit recomputes the routes
and rejects changed bands, evidence, filled answers or authorization. A failed
window retains native accounting and a failure question, and commits no partial
course records.

New prediction outputs include `precision_routes.jsonl`,
`precision_medium_review.jsonl`, `precision_omission_questions.jsonl` and
`precision_window_failures.jsonl`. Queues include exact candidate IDs, node IDs,
source checksum, physical page, CENTRAL/CONTEXT scope and evidence. Overlapping
context windows can repeat source observations; counts are not independent courses.
Existing `course_proposals.jsonl` remains a proposal cache requiring review.

## Permitted exposed measurement

The cached audit requires a registered exposed pilot/evaluation before reading
source proposals. It checks prediction and payload hashes, exact window inventory,
typed cached stages and native accounting. It computes and hashes source-only
routes **before** reading pinned, existing exposed candidate labels. No labels
select the policy or thresholds. Source/group/role binding is checked independently.

Two fixed nested selections, HIGH_ONLY and HIGH_PLUS_MEDIUM, measure the share of
existing central proposals selected and conditional reviewed-header precision.
Unknown and ambiguous alignments stay unknown. Conditional header recall excludes
unproposed headers; it is not complete-course recall. Zero denominators remain
undefined. TUNING and EXPOSED_DIAGNOSTIC results remain separate.

The final [report](../artifacts/precision-phase18-v1/release-v1/index.html) covers
68 exposed windows: 50 TUNING and 18 EXPOSED_DIAGNOSTIC. Fifty windows have source
reviews; 18 are pending source review. All 469 context/central records remain
unchanged. Across all windows, central routes contain 150 HIGH records, 22 MEDIUM
records, 339 LOW questions and 531 excluded references/noncourses.

| Fixed selection in the 50 reviewed windows | Existing central proposals | Proposal coverage | Reviewed true headers | Reviewed false headers | Ambiguous/unlabeled |
| --- | ---: | ---: | ---: | ---: | ---: |
| HIGH_ONLY | 86 | 89.6% | 83 | 0 | 3 |
| HIGH_PLUS_MEDIUM | 96 | 100% | 91 | 0 | 5 |

Both selections have 100% precision **among their labeled header alignments**.
The remaining alignments cannot be declared correct. HIGH still includes three
ambiguous alignments, demonstrating why it cannot authorize publication. The
unchanged strict results include one contaminated description, 66/128 correct
complete core records and 13/97 correct all-field records. These scores were
copied from the bound exposed evaluation; the strict scorer was not changed or
rerun and no complete-record precision improvement is inferred.

## Commands and verification

```sh
PYTHONPATH=src python -m course_parser audit-precision \
  --reference-project /path/to/University_Course_Catalog_Scraper \
  --pilot artifacts/table-ownership-v1/expanded-pilot \
  --evaluation artifacts/ocr-recovery-phase10-v1/candidate-v2/expanded-evaluation \
  --output /new/output/directory

python -m pytest tests --reference-project /path/to/University_Course_Catalog_Scraper
python -m pytest tests --regression-suite \
  --reference-project /path/to/University_Course_Catalog_Scraper
```

The permanent catalogue adds the precision-routing obligations. Meaningful checks
cover unsafe promotion despite perfect numeric confidence, creditless courses,
source-confirmed absence versus missing description, no-record and negative-entry
routing, source/identity corruption, tampered bands and filled answers, atomic
failure behavior, honest metric denominators, source/label read order, changed
cache checksums and reserved/unregistered input rejection. Final verification:
564 full-suite tests and 154 permanent-gate checks pass; all 17 frozen source
fixtures retain their pinned inventories and recovery/ambiguity contracts.

## Screenshot milestones and evaluation gate

The broader screenshot milestones describe historical exposed results. Earlier
phase work already addressed diagnosis, historical headers, omission ranking,
source ordering and model readiness. The proposed numeric targets are examples,
not release acceptance criteria; they must not replace the strict field contracts
or motivate institution-specific rules. The former 15 diagnostic examples remain
exposed development material. CRF/LayoutLM training remains deferred.

No new cohort is assembled, inspected or evaluated here. Phase 18 implementation
is not a parser freeze or proof of untouched generalization. The existing
18-phase execution seal is unchanged. The reserved 34-window institution-held-out
cohort and its review answers remain unopened; completing this phase alone does
not open the gate. A later explicit freeze and completion audit must precede any
permitted evaluation, and any subsequent tuning makes those results exposed.
