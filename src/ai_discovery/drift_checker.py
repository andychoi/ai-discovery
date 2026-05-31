"""
Drift detection for screen specs.

Detects when source files have changed since specs were generated.
Uses SHA256 hashes stored in frontmatter to compare against current code.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class DriftResult:
    """Result of drift check for a single screen."""

    screen_id: str
    spec_file: Path
    is_drifted: bool
    changed_files: list[str] = None  # List of files that changed
    missing_files: list[str] = None  # Files in hashes but no longer exist
    new_files: list[str] = None  # Files on disk but not in hashes
    error: Optional[str] = None  # Error message if check failed

    def __post_init__(self):
        if self.changed_files is None:
            self.changed_files = []
        if self.missing_files is None:
            self.missing_files = []
        if self.new_files is None:
            self.new_files = []


class DriftChecker:
    """Checks for drift between specs and source code."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)

    def check_spec(self, spec_file: Path) -> DriftResult:
        """
        Check drift for a single spec file.

        Reads source_hashes from frontmatter and compares against current files.
        """
        try:
            # Extract screen ID from filename
            screen_id = spec_file.stem  # e.g., customer-search.md -> customer-search

            # Parse frontmatter
            source_hashes = self._extract_source_hashes(spec_file)

            if not source_hashes:
                return DriftResult(screen_id=screen_id, spec_file=spec_file, is_drifted=False)

            # Check each file in hashes
            changed_files = []
            missing_files = []

            for file_path, stored_hash in source_hashes.items():
                full_path = self.repo_path / file_path

                if not full_path.exists():
                    missing_files.append(file_path)
                    continue

                # Compute current hash
                current_hash = self._compute_file_hash(full_path)

                if current_hash != stored_hash:
                    changed_files.append(file_path)

            is_drifted = bool(changed_files or missing_files)

            return DriftResult(
                screen_id=screen_id,
                spec_file=spec_file,
                is_drifted=is_drifted,
                changed_files=changed_files,
                missing_files=missing_files,
            )

        except Exception as e:
            return DriftResult(
                screen_id=spec_file.stem, spec_file=spec_file, is_drifted=False, error=str(e)
            )

    def check_specs(self, spec_dir: Path) -> list[DriftResult]:
        """
        Check drift for all spec files in a directory.

        Returns list of DriftResult objects.
        """
        results = []

        if not spec_dir.exists():
            return results

        for spec_file in spec_dir.glob("*.md"):
            result = self.check_spec(spec_file)
            results.append(result)

        return results

    def _extract_source_hashes(self, spec_file: Path) -> dict[str, str]:
        """
        Extract source_hashes from spec frontmatter.

        Returns dict of {file_path: hash}.
        """
        content = spec_file.read_text()

        # Extract YAML frontmatter
        if not content.startswith("---"):
            return {}

        # Find closing ---
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if not match:
            return {}

        frontmatter_text = match.group(1)

        try:
            frontmatter = yaml.safe_load(frontmatter_text)
        except Exception:
            return {}

        if not frontmatter:
            return {}

        # Extract source_hashes field
        source_hashes = frontmatter.get("source_hashes", {})

        if not isinstance(source_hashes, dict):
            return {}

        return source_hashes

    @staticmethod
    def _compute_file_hash(filepath: Path) -> str:
        """Compute SHA256 hash of a file."""
        try:
            content = filepath.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception:
            return ""

    def report(self, results: list[DriftResult]) -> str:
        """
        Generate human-readable drift report.

        Returns formatted report string.
        """
        lines = ["🔄 Drift Detection Report", "═" * 25, ""]

        drifted = [r for r in results if r.is_drifted]
        total = len(results)

        lines.append(f"Drifted screens: {len(drifted)} of {total}")
        lines.append("")

        for result in sorted(drifted, key=lambda r: r.screen_id):
            lines.append(f"  ⚠️  {result.screen_id}")

            if result.changed_files:
                lines.append(f"      Changed ({len(result.changed_files)}):")
                for file in result.changed_files[:5]:  # Show first 5
                    lines.append(f"        - {file}")
                if len(result.changed_files) > 5:
                    lines.append(f"        ... and {len(result.changed_files) - 5} more")

            if result.missing_files:
                lines.append(f"      Missing ({len(result.missing_files)}):")
                for file in result.missing_files[:3]:
                    lines.append(f"        - {file}")
                if len(result.missing_files) > 3:
                    lines.append(f"        ... and {len(result.missing_files) - 3} more")

            lines.append("")

        # Summary
        if drifted:
            lines.append(f"✗ {len(drifted)} screen(s) out of sync")
        else:
            lines.append("✓ All screens in sync")

        return "\n".join(lines)


def check_drift(repo_path: Path, spec_dir: Path) -> tuple[list[DriftResult], str]:
    """
    Check drift and return results + report.

    Returns (results, report_text).
    """
    checker = DriftChecker(repo_path)
    results = checker.check_specs(spec_dir)
    report = checker.report(results)
    return results, report
