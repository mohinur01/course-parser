# Share test fixtures and command-line options, and check that the permanent regression
# suite includes every required case.
from __future__ import annotations

import os
from pathlib import Path

import pytest
from regression_catalogue import catalogue, coverage_matches, verify_catalogue

from course_parser.reference import ReferenceProject


# Register the reference-project path and the permanent regression-gate options.
def pytest_addoption(parser):
    parser.addoption(
        "--reference-project", default=os.environ.get("COURSE_PARSER_REFERENCE_PROJECT")
    )
    parser.addoption(
        "--regression-suite", action="store_true", help="Run every permanent regression obligation"
    )


# Verify the pinned regression catalogue before any tests run.
def pytest_sessionstart(session):
    if session.config.getoption("--regression-suite"):
        # --regression-suite requires all obligations; omit -k and -m.
        if session.config.option.keyword or session.config.option.markexpr:
            raise pytest.UsageError("--regression-suite requires all obligations; omit -k and -m")
        try:
            verify_catalogue()
        except (ValueError, OSError, KeyError) as error:
            raise pytest.UsageError(str(error)) from error


# Require every catalogue obligation and select the full permanent regression gate when
# requested.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    registry = catalogue()
    selectors = {"tests/" + s for ss in registry["coverage"].values() for s in ss}
    for item in items:
        base = item.nodeid.split("[")[0]
        if base in selectors or item.nodeid.split("::")[0] in selectors:
            item.add_marker(pytest.mark.regression)
    if config.getoption("--regression-suite"):
        try:
            coverage_matches([item.nodeid for item in items], registry)
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        selected = [item for item in items if item.get_closest_marker("regression")]
        deselected = [item for item in items if item not in selected]
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


# Fail the permanent gate when required checks were skipped or did not pass.
def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("--regression-suite"):
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if reporter and any(reporter.stats.get(s) for s in ("skipped", "xfailed", "xpassed")):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


# Print whether the complete permanent regression gate met its coverage requirements.
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if config.getoption("--regression-suite"):
        terminalreporter.write_line(
            "Permanent regression gate: every catalogue obligation required; skips/xfails fail."
        )


# Load the explicitly selected reference project for the shared test fixtures.
@pytest.fixture(scope="session")
def reference(request):
    root = request.config.getoption("--reference-project")
    if not root:
        pytest.fail("Pass --reference-project to test the actual v3 contracts and frozen matcher")
    return ReferenceProject(Path(root))
