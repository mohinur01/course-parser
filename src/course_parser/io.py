# Read and write the files shared by the project. Check recorded file fingerprints (SHA-256
# checksums) and allowed paths before trusting saved inputs or results.
"""Small, strict artifact helpers shared by preparation and verification."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


# Calculate a file fingerprint from its bytes so later checks can detect any change.
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        # Read large files in small chunks so calculating a fingerprint does not load the
        # whole file into memory.
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# Read one JSON document from a UTF-8 file.
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# Read a CSV table and reject missing, duplicated or malformed columns.
def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        # Reject missing or duplicate CSV column names.
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError(f"missing or duplicate CSV column names: {path}")
        rows = list(reader)
    # Every CSV row must have the expected number of columns.
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"malformed CSV row: {path}")
    return rows


# Create a new JSON file without overwriting an existing artifact.
def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


# Create a new JSON-lines file with one complete record per line.
def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# Read the nonempty lines of a JSON-lines file as separate records.
def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# Read answers only for the selected page IDs. Inspect each row's ID before decoding its
# review payload.
def read_selected_jsonl(path: Path, selected_ids: set[str]) -> list[dict]:
    """Gate a shared review snapshot by page ID before decoding answer payloads.

    Hashing/streaming shared bytes is permitted for provenance; answers belonging
    to other cohorts must never be deserialized or sent to the parser/evaluator.
    The source-review schema contains one dataset_page_id per record.
    """
    page_key = re.compile(rb'"dataset_page_id"\s*:\s*"([A-Za-z0-9_:-]+)"')
    records = []
    with path.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            keys = page_key.findall(line)
            # Each review row needs exactly one page ID so it can be checked before decoding
            # its answers.
            if len(keys) != 1:
                raise ValueError("review snapshot must contain one safe page identity per row")
            # Decode only the selected pages. Other rows may contain answers that this
            # workflow is not allowed to inspect.
            if keys[0].decode("ascii") in selected_ids:
                value = json.loads(line)
                # Recheck the decoded page ID so only the permitted page's answers are
                # returned.
                if value["dataset_page_id"] not in selected_ids:
                    raise ValueError("selected review identity differs from its payload")
                records.append(value)
    return records


# Write the requested table columns to a new CSV artifact.
def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


# Resolve a file inside its allowed folder and reject absolute paths or paths that escape that
# folder.
def contained_file(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    resolved_root = root.resolve()
    path = (resolved_root / candidate).resolve()
    # Reject a path or coordinate that falls outside its allowed source boundary.
    if candidate.is_absolute() or not path.is_relative_to(resolved_root) or path == resolved_root:
        raise ValueError(f"artifact path escapes its root: {relative}")
    return path


# Compare each saved file with its recorded fingerprint before trusting its contents.
def verify_files(root: Path, hashes: dict[str, str]) -> None:
    # Require recorded file fingerprints before trusting this saved input.
    if not hashes:
        raise ValueError("manifest contains no artifact hashes")
    for name, expected in hashes.items():
        path = contained_file(root, name)
        # Reject this input if its file fingerprint no longer matches the recorded version.
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"artifact missing or checksum changed: {name}")


# Record the fingerprints of the files in an output folder so later checks can detect changes.
def artifact_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# Translate an approved remote source path to its configured local copy.
def map_source_path(value: str, remote_root: Path | None, local_root: Path | None) -> Path:
    # Both source mapping roots are required.
    if (remote_root is None) != (local_root is None):
        raise ValueError("both source mapping roots are required")
    path = Path(value)
    if remote_root and local_root and path.is_relative_to(remote_root):
        return local_root / path.relative_to(remote_root)
    return path
