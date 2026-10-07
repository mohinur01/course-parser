# Create a small example using invented source data so the evidence workflow can be inspected
# without relying on a real catalog.
"""Create an unmistakably synthetic, inspectable first-milestone example."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures import create_fixture  # noqa: E402

from course_parser.bridge import prepare_bridge, verify_run  # noqa: E402
from course_parser.reference import ReferenceProject  # noqa: E402
from course_parser.release import TaskRelease  # noqa: E402


# Read the command-line options, then create a small example using invented source data so the
# evidence workflow can be inspected without relying on a real catalog.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference = ReferenceProject(args.reference_project)
    fixture = create_fixture(args.output / "synthetic-inputs", reference)
    release = TaskRelease.load(fixture.release, fixture.vocabulary, reference)
    packet = args.output / "source-packet"
    reference.materialize(fixture.release, packet, start_rank=1, count=2)
    result = prepare_bridge(reference, release, packet, args.output / "evidence")
    result["verification"] = verify_run(args.output / "evidence", recheck_inputs=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
