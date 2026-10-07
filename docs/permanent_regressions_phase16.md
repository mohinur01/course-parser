# Phase 16: permanent behavioral regressions

The recommendation is appropriate. Most listed behaviors already have meaningful
tests, so duplicating them under new names would add little protection. This phase
organizes those tests into an explicit permanent gate and strengthens the existing
source replay oracle. It changes test infrastructure, not extraction behavior.
No parser accuracy or untouched generalization gain is claimed.

## What the gate protects

`config/regression_suite_v1.json` maps all twelve suggested behaviors to existing
test functions: requirements and prerequisite references remain references;
historical numeric and creditless courses remain detectable; titles reconstruct
across lines; descriptions exclude the next header; prerequisites remain separate;
columns and page continuation preserve supported ownership; native source survives;
crop coordinates map to the PDF; omission review never automatically accepts courses.
Two additional groups protect reserved-access/annotation integrity and the
regression catalogue itself. Synthetic tests isolate these behaviors and negative
controls. Existing catalog snapshots exercise the integrated parser.

The Phase 16 acceptance gate ran 82 test instances across 14 coverage groups.
Phase 17 adds mandatory accounting regressions: the current gate runs 110 instances
across 15 groups; see [whole-page accounting](page_accounting_phase17.md).
Parametrization includes rotated crops and negative cases, rather than twelve
superficial boolean checks. Optional external OCR executables are not required by
the permanent gate. Existing source-reviewed continuation tests exercise supported
continuation; experimental margin-based recovery remains review-only.

Run the complete gate from this workspace:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests \
  --regression-suite \
  --reference-project /path/to/University_Course_Catalog_Scraper \
  --junitxml=/path/to/new-regression-results.xml -q
```

The external reference project supplies the existing typed contracts and renderer,
as in the ordinary suite. Keep `tests` explicit so pytest discovers this workspace's
configuration before interpreting the external project path. The gate refuses
`-k`/`-m` filters, missing obligations, skips and xfails. A deliberate skip probe
returned failure, and a deliberately incomplete test invocation failed collection.
For debugging, run individual tests without `--regression-suite`; such a run is
not the complete regression gate. Registered markers are checked strictly.

The ordinary full-suite command also collects all regression tests:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests \
  --reference-project /path/to/University_Course_Catalog_Scraper -q
```

There is no configured repository CI service to connect in this workspace. These
commands are suitable local or CI checks when the reference project is available;
no remote CI run is claimed.

## Stronger source oracles

Seventeen existing permitted fixture files in six inventories are pinned by their
existing manifest hashes. Missing, changed, added or symlink-redirected JSON cases
fail before source replay. Rewriting a fixture and regenerating its local manifest
does not silently update the pinned catalogue. Explicit inventory enumeration
replaces glob-only parametrization, which could silently lose cases.

Twelve complete source inventories now protect 76 existing recovered gold-ID
observations, including 66 distinct IDs. Each protected observation must still
agree with its original source-reviewed core fields and boundary/continuation
contract through the unchanged strict scorer. A newly recovered course cannot
compensate for losing an existing one merely by maintaining the same count.
Additional valid recovery remains allowed; the original count floors remain as
secondary checks. Expected raw field answers were neither regenerated nor copied
from parser output. The selected gold IDs document current recovery capabilities,
not newly annotated truth or independent certification.

The complete inventories also reject newly emitted unaligned, nonambiguous
headers. Three snapshots currently retain alignment ambiguity, with ceilings of
one, two and one emitted headers. These are recorded limitations, not accepted
gold courses. Ambiguity cannot grow unnoticed, and a future source-supported fix
may lower a ceiling. One partial source observation remains a targeted competing
ownership test and is never treated as a complete scored inventory.

The catalogue tests demonstrate that equal-count identity replacement, new false
emission, ambiguity growth, fixture deletion, changed answers, manifest rewriting,
unregistered cases and missing test obligations fail. Production parser and strict
scoring modules remain byte-for-byte unchanged.

## Maintaining the suite

For a source-supported bug, add the smallest useful permitted regression and
assert both the intended recovery and the relevant negative behavior. Prefer
source identity, exact spans, conservation and ownership invariants over snapshots
of all parser output. Record the bug, fixture provenance, exposure role and test
selector in the catalogue. Add whole-source cases only when permitted review
evidence supports their expected answers; incomplete observations may test a
narrow invariant but cannot become complete gold.

When intended behavior changes, review the source evidence and revise the relevant
test/contract explicitly. Do not automatically refresh expected answers or lower
recovery floors to obtain a pass. Retain bug protection while allowing justified
improvements. Run both the complete regression gate and the full suite before
acceptance. The gate uses TUNING, already exposed diagnostics and synthetic
regressions; it is development evidence, not a new evaluation cohort.

The reserved 34-window institution-held-out cohort and its review answers were
not accessed. They remain sealed until all 18 phases are complete and the parser
is frozen. New regression work must continue to use permitted material.

See the [Phase 16 report](../artifacts/regression-phase16-v1/release-v1/index.html)
for coverage, execution results and artifact provenance.
