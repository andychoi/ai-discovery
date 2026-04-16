"""Tests for staleness detection and duplicate detection."""

import pytest
from pathlib import Path

from app.ingest.dedup import detect_stale_files, detect_splittable
from app.ingest.walker import IngestFile


def _make_file(name: str, content: str = "Some content", doc_type: str = "design") -> IngestFile:
    return IngestFile(
        path=Path(name), content=content, title=name,
        domain="test", doc_type=doc_type,
    )


class TestStalenessDetection:
    def test_filename_old_suffix(self):
        files = [_make_file("auth-design-old.md")]
        detect_stale_files(files)
        assert files[0].stale is True
        assert "stale indicator" in files[0].stale_reason

    def test_filename_draft_suffix(self):
        files = [_make_file("auth-design-draft.md")]
        detect_stale_files(files)
        assert files[0].stale is True

    def test_filename_archived_suffix(self):
        files = [_make_file("auth-design_archived.md")]
        detect_stale_files(files)
        assert files[0].stale is True

    def test_content_deprecated(self):
        files = [_make_file("design.md", content="This document is deprecated.")]
        detect_stale_files(files)
        assert files[0].stale is True
        assert "content matches" in files[0].stale_reason

    def test_content_superseded(self):
        files = [_make_file("design.md", content="Superseded by DES-002.")]
        detect_stale_files(files)
        assert files[0].stale is True

    def test_version_clustering(self):
        files = [
            _make_file("auth-v1.md"),
            _make_file("auth-v2.md"),
            _make_file("auth-v3.md"),
        ]
        detect_stale_files(files)
        # v1 and v2 should be stale (older versions)
        assert files[0].stale is True  # v1
        assert files[1].stale is True  # v2
        assert files[2].stale is False  # v3 (latest)

    def test_clean_file_not_stale(self):
        files = [_make_file("auth-design.md", content="A clean design document.")]
        detect_stale_files(files)
        assert files[0].stale is False

    def test_content_replaced_by(self):
        files = [_make_file("old.md", content="This has been replaced by the new version.")]
        detect_stale_files(files)
        assert files[0].stale is True


class TestSplitDetection:
    def test_large_mixed_file(self):
        # Large file with strong signals for both design and spec
        content = "# Big Doc\n\n" + "x " * 3000  # >5000 chars
        content += "\n## Architecture Diagram\n## Component Design\n## Data Flow\n"
        content += "\n## Validation Rules\n## Error Cases\n## Expected Output\n"
        files = [_make_file("big-doc.md", content=content)]
        suggestions = detect_splittable(files)
        assert len(suggestions) >= 1
        assert len(suggestions[0].detected_types) >= 2

    def test_small_file_not_suggested(self):
        content = "# Small\n\n## Architecture Diagram\n## Validation Rules\n"
        files = [_make_file("small.md", content=content)]
        suggestions = detect_splittable(files)
        assert len(suggestions) == 0  # too small
