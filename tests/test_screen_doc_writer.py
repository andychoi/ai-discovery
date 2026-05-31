"""Tests for screen spec markdown writing and MANUAL block preservation."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.ai.screen_spec_generator import ScreenSpec
from ai_discovery.generators.screen_doc_writer import (
    load_existing_manual_blocks,
    write_screen_spec,
)
from ai_discovery.menu_detector import Screen
from ai_discovery.screen_mapper import ScreenMapping


@pytest.fixture
def temp_docs_dir():
    """Create a temporary docs directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestManualBlockPreservation:
    """Test loading and preserving MANUAL blocks."""

    def test_load_manual_blocks_no_file(self, temp_docs_dir):
        """Test loading MANUAL blocks when spec file doesn't exist."""
        blocks = load_existing_manual_blocks("nonexistent", temp_docs_dir)
        assert blocks == {}

    def test_load_manual_blocks_no_manual_sections(self, temp_docs_dir):
        """Test loading MANUAL blocks from spec without any."""
        screens_dir = temp_docs_dir / "screens"
        screens_dir.mkdir(parents=True)

        spec_file = screens_dir / "test.md"
        spec_file.write_text("# Test Screen\nNo MANUAL blocks here.")

        blocks = load_existing_manual_blocks("test", temp_docs_dir)
        assert blocks == {}

    def test_load_manual_blocks_single_block(self, temp_docs_dir):
        """Test loading a single MANUAL block."""
        screens_dir = temp_docs_dir / "screens"
        screens_dir.mkdir(parents=True)

        spec_content = """# Test Screen

<!-- MANUAL:business-context -->
This is a user note about the business context.
<!-- /MANUAL:business-context -->

## Purpose
Auto-generated content here.
"""
        spec_file = screens_dir / "test.md"
        spec_file.write_text(spec_content)

        blocks = load_existing_manual_blocks("test", temp_docs_dir)

        assert "business-context" in blocks
        assert "user note" in blocks["business-context"]

    def test_load_manual_blocks_multiple_blocks(self, temp_docs_dir):
        """Test loading multiple MANUAL blocks."""
        screens_dir = temp_docs_dir / "screens"
        screens_dir.mkdir(parents=True)

        spec_content = """# Test Screen

<!-- MANUAL:business-context -->
Business context notes.
<!-- /MANUAL:business-context -->

<!-- MANUAL:layout-notes -->
Layout notes.
<!-- /MANUAL:layout-notes -->
"""
        spec_file = screens_dir / "test.md"
        spec_file.write_text(spec_content)

        blocks = load_existing_manual_blocks("test", temp_docs_dir)

        assert len(blocks) == 2
        assert "business-context" in blocks
        assert "layout-notes" in blocks
        assert blocks["business-context"] == "Business context notes."
        assert blocks["layout-notes"] == "Layout notes."


class TestScreenSpecWriting:
    """Test writing screen specs to markdown files."""

    def test_write_screen_spec_creates_file(self, temp_docs_dir):
        """Test that write_screen_spec creates the output file."""
        screen = Screen(
            screen_id="test-screen",
            menu_path=["Test", "Screen"],
            label="Test Screen",
            path="/test",
        )
        spec = ScreenSpec(
            screen_id="test-screen",
            screen_label="Test Screen",
            menu_path=["Test", "Screen"],
            purpose="Test purpose",
            when_used="For testing",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="No rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
            source_hashes={"src/test.tsx": "abc123"},
        )

        output_path = write_screen_spec(
            spec,
            temp_docs_dir,
            "myapp",
            repo_url="https://github.com/test/repo",
            repo_commit="abc123def456",
        )

        assert output_path.exists()
        assert output_path.name == "test-screen.md"
        assert output_path.parent.name == "screens"

    def test_write_screen_spec_includes_frontmatter(self, temp_docs_dir):
        """Test that output includes YAML frontmatter."""
        screen = Screen(
            screen_id="test",
            menu_path=["Test"],
            label="Test",
            path="/test",
        )
        spec = ScreenSpec(
            screen_id="test",
            screen_label="Test Screen",
            menu_path=["Test"],
            purpose="Test",
            when_used="When",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="Rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
            source_hashes={"file.tsx": "hash1"},
        )

        output_path = write_screen_spec(spec, temp_docs_dir, "myapp")
        content = output_path.read_text()

        assert content.startswith("---")
        assert "doc_id:" in content
        assert "myapp-screen-test" in content
        assert "source_hashes:" in content

    def test_write_screen_spec_preserves_manual_blocks(self, temp_docs_dir):
        """Test that existing MANUAL blocks are preserved."""
        screens_dir = temp_docs_dir / "screens"
        screens_dir.mkdir(parents=True)

        # Create an existing spec with a MANUAL block
        existing_spec = """---
doc_id: myapp-screen-test
title: Test
---

# Test Screen

<!-- MANUAL:business-context -->
Important business context that should be preserved.
<!-- /MANUAL:business-context -->

## Purpose
Auto-generated.
"""
        (screens_dir / "test.md").write_text(existing_spec)

        # Write new spec (should preserve MANUAL block)
        screen = Screen(
            screen_id="test",
            menu_path=["Test"],
            label="Test",
            path="/test",
        )
        spec = ScreenSpec(
            screen_id="test",
            screen_label="Test Screen",
            menu_path=["Test"],
            purpose="New purpose",
            when_used="When",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="Rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
        )

        write_screen_spec(spec, temp_docs_dir, "myapp")
        content = (screens_dir / "test.md").read_text()

        # Verify MANUAL block is preserved
        assert "Important business context" in content
        assert "MANUAL:business-context" in content

    def test_write_screen_spec_includes_title(self, temp_docs_dir):
        """Test that output includes screen title."""
        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Customer Search Screen",
            path="/customers/search",
        )
        spec = ScreenSpec(
            screen_id="customer-search",
            screen_label="Customer Search Screen",
            menu_path=["Customers", "Search"],
            purpose="Test",
            when_used="When",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="Rules",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
        )

        output_path = write_screen_spec(spec, temp_docs_dir, "myapp")
        content = output_path.read_text()

        assert "# Customer Search Screen" in content or "title:" in content


class TestScreenSpecIntegration:
    """Integration tests for spec generation and writing."""

    def test_full_spec_workflow(self, temp_docs_dir):
        """Test full workflow: create spec, write to disk, reload blocks."""
        # Create and write initial spec
        screen = Screen(
            screen_id="workflow-test",
            menu_path=["Workflow", "Test"],
            label="Workflow Test Screen",
            path="/workflow/test",
        )
        spec = ScreenSpec(
            screen_id="workflow-test",
            screen_label="Workflow Test Screen",
            menu_path=["Workflow", "Test"],
            purpose="Test workflow",
            when_used="For integration testing",
            interaction_mode="workflow_step",
            crud_profile="create_edit",
            user_actions=[
                {"action": "Enter data", "response": "Validate and save"}
            ],
            rules_narrative="Standard validation rules apply",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
        )

        output_path = write_screen_spec(spec, temp_docs_dir, "testapp")
        assert output_path.exists()

        # Manually update the MANUAL block in the written file
        content = output_path.read_text()
        # Replace the default MANUAL block with custom content
        content = content.replace(
            """<!-- MANUAL:business-context -->
<!-- Insert additional business context here -->
<!-- /MANUAL:business-context -->""",
            """<!-- MANUAL:business-context -->
Add your business context notes here.
<!-- /MANUAL:business-context -->""",
        )
        output_path.write_text(content)

        # Now load blocks and verify they're preserved
        blocks = load_existing_manual_blocks("workflow-test", temp_docs_dir)
        assert "business-context" in blocks
        assert "business context notes" in blocks["business-context"]
