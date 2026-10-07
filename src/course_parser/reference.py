# Load the reference project definitions, exact keyword matcher and page renderer. Check their
# versions and fingerprints so this project uses the intended source contracts.
"""Reuse the user's existing v3 contracts and exact matcher without editing them."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from .io import read_json, sha256


# Provide access to the checked reference definitions, keyword matcher and PDF renderer.
class ReferenceProject:
    # Store the inputs and helpers this object uses in its later methods.
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.source = self.root / "src"
        required = (
            "catalog_mining/course_extraction_v3/models.py",
            "catalog_mining/search/supervisor_keywords.py",
            "catalog_mining/search/supervisor_keywords_v2.py",
        )
        # Stop if the reference project does not contain v3 contracts and the v2 matcher.
        if not all((self.source / relative).is_file() for relative in required):
            raise ValueError("reference project does not contain v3 contracts and the v2 matcher")
        existing = sys.modules.get("catalog_mining")
        # Stop if a different catalog_mining project is already imported.
        if existing and not Path(existing.__file__).resolve().is_relative_to(self.source):
            raise ValueError("a different catalog_mining project is already imported")
        sys.path.insert(0, str(self.source))
        self.models = importlib.import_module("catalog_mining.course_extraction_v3.models")
        self.matcher = importlib.import_module("catalog_mining.search.supervisor_keywords_v2")
        self.normalize = importlib.import_module(
            "catalog_mining.search.supervisor_keywords"
        ).normalize_course_text

    # Require the supplied keyword vocabulary to match the frozen exact matcher and its
    # policies.
    def vocabulary(self, path: Path) -> dict:
        value = read_json(path)
        expected = self.matcher.build_vocabulary()
        # Expected the frozen 33-concept, 68-form vocabulary v2.
        if (
            value.get("schema_version") != "supervisor-keyword-vocabulary-v2"
            or value.get("canonical_concept_count") != 33
            or value.get("accepted_variant_count") != 68
        ):
            raise ValueError("expected the frozen 33-concept, 68-form vocabulary v2")
        keys = ("concept_id", "canonical_phrase", "accepted_variants", "pattern")
        actual_concepts = [{key: item[key] for key in keys} for item in value["concepts"]]
        expected_concepts = [{key: item[key] for key in keys} for item in expected["concepts"]]
        # Stop if the vocabulary differs from the authoritative reference matcher.
        if actual_concepts != expected_concepts or len(actual_concepts) != 33:
            raise ValueError("vocabulary differs from the authoritative reference matcher")
        for key in ("normalization", "scope", "cross_field_policy", "overlap_policy"):
            # Stop if the vocabulary policy differs from the reference.
            if value.get(key) != expected.get(key):
                raise ValueError(f"vocabulary policy differs from the reference: {key}")
        return value

    # Record checksums of the reference contracts, matcher and renderer used by this run.
    def provenance(self) -> dict[str, str]:
        paths = list((self.source / "catalog_mining/course_extraction_v3").glob("*.py"))
        paths += list((self.source / "catalog_mining/search").glob("supervisor_keywords*.py"))
        paths += [
            self.root / "scripts" / name
            for name in ("materialize_page_review_batch_v1.py", "build_page_review_tasks_v1.py")
        ]
        return {str(path): sha256(path) for path in sorted(paths) if path.is_file()}

    # Use the verified reference renderer to create the requested bounded page batch.
    def materialize(
        self,
        release: Path,
        output: Path,
        *,
        start_rank: int,
        count: int,
        remote_root: Path | None = None,
        local_root: Path | None = None,
    ) -> dict:
        scripts = self.root / "scripts"
        for name in ("build_page_review_tasks_v1", "materialize_page_review_batch_v1"):
            cached = sys.modules.get(name)
            # Stop if a renderer from another reference project is already imported.
            if cached and Path(cached.__file__).resolve().parent != scripts:
                raise ValueError("a renderer from another reference project is already imported")
        sys.path.insert(0, str(scripts))
        path = scripts / "materialize_page_review_batch_v1.py"
        spec = importlib.util.spec_from_file_location("materialize_page_review_batch_v1", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.materialize(
            release,
            output,
            start_rank=start_rank,
            count=count,
            remote_root=remote_root,
            local_root=local_root,
        )

    # Create a typed page-source record using the original extraction channel and page image.
    def page_source(
        self,
        *,
        source_id: str,
        checksum: str,
        page: dict,
        image_path: Path,
        image_hash: str,
        width: float,
        height: float,
        rotation: float,
        native_text: str,
    ):
        m = self.models
        raw = page.get("raw_text") or ""
        kind = str(page.get("extraction_type", "")).lower()
        if kind in {"embedded", "native", "pymupdf", "embedded_text"}:
            channel = m.SourceChannel.EMBEDDED_TEXT
            method = (
                m.ExtractionMethod.PYMUPDF_EMBEDDED_TEXT
                if native_text == raw
                else m.ExtractionMethod.MANIFEST
            )
        elif kind == "embedded_ocr":
            channel, method = m.SourceChannel.EMBEDDED_OCR, m.ExtractionMethod.PDF_EMBEDDED_OCR
        elif kind in {"ocr", "tesseract", "tesseract_ocr", "image_ocr"}:
            channel, method = m.SourceChannel.IMAGE_OCR, m.ExtractionMethod.MANIFEST
        else:
            raise ValueError(f"unresolved frozen extraction channel: {kind!r}")
        number = int(page["page_number"])
        representation = m.TextRepresentation(
            representation_id=f"{source_id}:{checksum}:p{number}:frozen",
            physical_page=number,
            channel=channel,
            extraction_method=method,
            raw_text=raw,
        )
        return m.PageSource(
            physical_page=number,
            width=width,
            height=height,
            rotation_degrees=rotation,
            image_path=str(image_path),
            image_sha256=image_hash,
            representations=(representation,),
        )

    # Link an exact source-text span to its physical page and extraction representation.
    def anchor(self, source_id: str, checksum: str, page_source, start: int, end: int):
        m = self.models
        representation = page_source.representations[0]
        # Stop if the original hit span is outside frozen page text.
        if not 0 <= start < end <= len(representation.raw_text):
            raise ValueError("original hit span is outside frozen page text")
        anchor = m.EvidenceAnchor(
            source_document_id=source_id,
            source_pdf_checksum=checksum,
            physical_page=page_source.physical_page,
            representation_id=representation.representation_id,
            observed_text=representation.raw_text[start:end],
            extraction_method=representation.extraction_method,
            text_span=m.TextSpan(start=start, end=end),
        )
        anchor.validate_against(representation)
        return anchor
