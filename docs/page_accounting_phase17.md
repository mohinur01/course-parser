# Phase 17: whole-page accounting

The recommendation is appropriate as a retention safeguard. Existing layout
conservation protects native cells during interpretation, and course-field links
partition owned course blocks. Neither gives all unowned text a final disposition.
Phase 17 fills that gap with a mandatory source ledger, without forcing uncertain
text into a course. Mutation regressions demonstrate that deleted, duplicated,
changed, unassigned and falsely owned source spans cannot pass the audit.
This is evidence of stronger loss detection, not measured extraction accuracy.

## Implemented contract

Every native extracted cell on every context page receives one or more newline
rows, including empty cells, whitespace, Unicode and original newline characters.
Rows retain document/PDF identity, physical page, node ID, exact character offsets,
raw text, observed and interpreted roles, and span-level evidence anchors.
A single cell can contain several course-field spans, but cannot belong to two
course records. Existing compound identifiers within one record remain supported.
There is no explicit shared-row ownership contract, so multiple owners fail.

Course spans keep the existing occurrence/block/allocation identities and field
roles: code, title, credits, description, prerequisites, other metadata and
separators. They also retain record-field membership and recovery/withheld status;
allocation does not turn a withheld record field into a recovered one. Unowned
spans become requirement text, course references, section headers, page furniture,
table text or unresolved proposals. A requirement-table reference is recorded as
requirement text. Withheld course blocks remain unresolved. Native furniture is
preserved under the existing `PAGE_FURNITURE` role: the source contracts do not
reliably distinguish headers from footers, so this phase does not invent that split.

The independent audit reconstructs the native row inventory and character coverage.
It rejects changed source identity/evidence, missing or duplicated rows, gaps,
overlaps, unknown dispositions, fabricated owners and hidden field allocations.
It compares the adopted graph with the native graph before accepting the ledger.
`MODEL_PROPOSAL` applies to every semantic disposition. `PASS` certifies retention
only; unresolved text is not a confirmed omission or a negative course label.

## Pipeline and failure behavior

All `run-learning-baseline` modes now require this audit before emitting a window's
course records. New runs save the ledger in both `page_proposals.jsonl` and
`page_accounting.jsonl`, with a required-accounting flag in the prediction manifest.
A failed field or ownership stage retains a source-only ledger and emits no courses
for that window. Earlier native pages survive a later extraction failure; missing
context pages are explicitly listed with `PARTIAL_OR_UNAVAILABLE`. If no valid
native graph exists, accounting is explicitly unavailable, never an empty success.
Empty extracted page inventories are separately flagged for completeness review.

These rows describe the text cells actually extracted from the source. They are
not guaranteed to be all visible lines on the PDF: absent text layers, OCR damage,
merged visual lines and missing scans still require separate source recovery.
No new OCR, semantic model, correction or automatic course creation was added.

## Permitted cache audit

`audit-page-accounting` uses only a registered exposed evaluation's bound source-only
prediction cache. It checks approval and split metadata before cached source reads,
then checks manifest/payload hashes and exact window/page/source identities.
It reads native/adopted layouts, candidate decisions, blocks and course proposals;
it does not import review answers or score courses. Reserved paths and unregistered
evaluations are rejected before source payload access.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m course_parser audit-page-accounting \
  --reference-project /path/to/University_Course_Catalog_Scraper \
  --pilot artifacts/table-ownership-v1/expanded-pilot \
  --evaluation artifacts/ocr-recovery-phase10-v1/candidate-v2/expanded-evaluation \
  --output /path/to/new-accounting-report
```

The fixed exposed cache contains 50 TUNING and 18 exposed diagnostic windows.
Its audit retained **18,624 source cells, 25,086 newline rows and 581,776 characters**.
All 469 cached course records remain unchanged. There are **16,142 unresolved rows**
and **nine empty context-page inventories**. Those are visible source/classification
questions, not proof of missed courses. Counts include overlapping context windows;
they do not represent independent pages or corpus accuracy.

| Proposed disposition | Spans |
| --- | ---: |
| Course field | 5,210 |
| Requirement text | 1,350 |
| Course reference | 1,024 |
| Section header | 861 |
| Page furniture | 141 |
| Table | 2,060 |
| Unresolved | 16,142 |

Span counts exceed row counts when a row contains multiple fields. The accounting
cache audit does not change extraction, boundaries, ordering or the strict scorer.
No new correctness score or generalization claim is made.

## Regression and acceptance evidence

Twenty-eight new test instances cover exact retention, multiline/empty cells,
source mutation, row loss/duplication, gap/overlap, evidence changes, false ownership,
hidden allocations, withheld blocks, tables, continuation, empty inventories,
partial extraction and downstream failure, plus reserved/unregistered cache access.
Integrated source replays also run the accounting audit against their native graph.
Existing source fixture hashes, answers and recovery floors remain unchanged.

The permanent gate now contains **110 passing instances across 15 coverage groups**;
the full suite contains **520 passing tests**. Ruff checks pass. The existing
Phase 16 report remains the historical 82-instance gate result; the new accounting
coverage is registered in `config/regression_suite_v1.json`.

The reserved 34-window institution-held-out cohort, its review answers and its
source assets were not inspected, opened, scored, summarized or used. The phase
execution seal remains in force until all 18 phases are complete and the parser
is frozen. Phase 17 does not freeze the parser or open that gate.

See the [Phase 17 report](../artifacts/page-accounting-phase17-v1/release-v4/index.html),
[ledger implementation](../src/course_parser/page_accounting.py), and
[regression tests](../tests/test_page_accounting.py).
