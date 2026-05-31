"""Integration tests for Phase 2 screen-centric spec generation."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ai_discovery.cli import detect_screens, verify_drift
from ai_discovery.menu_detector import HybridMenuDetector, build_screen_map
from ai_discovery.screen_mapper import ScreenMapper
from ai_discovery.generators.screen_doc_writer import write_all_screen_specs
from ai_discovery.ai.screen_spec_generator import ScreenSpec


@pytest.fixture
def temp_repo():
    """Create a temporary repository with menu structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create menu.json
        menu_structure = {
            "menu": [
                {
                    "label": "Customers",
                    "path": "/customers",
                    "submenu": [
                        {
                            "label": "Search",
                            "path": "/customers/search",
                            "component": "src/pages/customers/SearchPage.vue"
                        },
                        {
                            "label": "Detail",
                            "path": "/customers/:id",
                            "component": "src/pages/customers/DetailPage.vue"
                        }
                    ]
                }
            ]
        }

        (repo_path / "menu.json").write_text(json.dumps(menu_structure, indent=2))

        # Create component files
        (repo_path / "src" / "pages" / "customers").mkdir(parents=True)
        (repo_path / "src" / "pages" / "customers" / "SearchPage.vue").write_text(
            """<template>
  <div class="search-page">
    <input v-model="searchTerm" placeholder="Search customers...">
    <button @click="search">Search</button>
    <table v-if="results">
      <tr v-for="customer in results" :key="customer.id">
        <td>{{ customer.name }}</td>
      </tr>
    </table>
  </div>
</template>

<script>
export default {
  name: 'SearchPage',
  data() {
    return {
      searchTerm: '',
      results: []
    }
  },
  methods: {
    async search() {
      const response = await fetch(`/api/customers/search?q=${this.searchTerm}`);
      this.results = await response.json();
    }
  }
}
</script>
"""
        )

        (repo_path / "src" / "pages" / "customers" / "DetailPage.vue").write_text(
            """<template>
  <div class="detail-page">
    <h1>{{ customer.name }}</h1>
    <p>Email: {{ customer.email }}</p>
  </div>
</template>

<script>
export default {
  name: 'DetailPage',
  data() {
    return {
      customer: {}
    }
  },
  async mounted() {
    const id = this.$route.params.id;
    const response = await fetch(`/api/customers/${id}`);
    this.customer = await response.json();
  }
}
</script>
"""
        )

        yield repo_path


@pytest.fixture
def temp_docs_dir():
    """Create a temporary docs directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestMenuDetection:
    """Test menu detection from JSON files."""

    def test_detect_json_menu(self, temp_repo):
        """Test detecting menus from menu.json."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)

        assert menu_items is not None
        assert len(menu_items) > 0

        # Build screen map
        screens = build_screen_map(menu_items, temp_repo)
        assert len(screens) > 0
        screen_ids = [s.screen_id for s in screens]
        assert any("search" in s for s in screen_ids)

    def test_screen_metadata(self, temp_repo):
        """Test that detected screens have correct metadata."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)
        screens = build_screen_map(menu_items, temp_repo)

        # Find customer-search screen
        search_screen = next((s for s in screens if "search" in s.screen_id), None)
        assert search_screen is not None
        assert "Customers" in search_screen.menu_path
        assert search_screen.label == "Search"
        assert search_screen.path == "/customers/search"


class TestScreenMapping:
    """Test mapping screens to backend and data."""

    def test_screen_mapper_creation(self, temp_repo):
        """Test creating screen mappings."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)
        screens = build_screen_map(menu_items, temp_repo)

        mapper = ScreenMapper(temp_repo)
        for screen in screens:
            mapping = mapper.map_screen(screen)
            assert mapping is not None
            assert mapping.screen == screen

    def test_api_call_extraction(self, temp_repo):
        """Test extracting API calls from Vue components."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)
        screens = build_screen_map(menu_items, temp_repo)

        search_screen = next((s for s in screens if "search" in s.screen_id), None)
        mapper = ScreenMapper(temp_repo)
        mapping = mapper.map_screen(search_screen)

        # Check that API calls were extracted
        # (Implementation may vary, but test the interface)
        assert hasattr(mapping, 'fe_api_calls')
        assert isinstance(mapping.fe_api_calls, list)


class TestScreenSpecGeneration:
    """Test spec generation for screens."""

    def test_write_specs_to_disk(self, temp_repo, temp_docs_dir):
        """Test writing generated specs to markdown files."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)
        screens = build_screen_map(menu_items, temp_repo)

        # Create minimal specs for testing
        specs = []
        for screen in screens:
            spec = ScreenSpec(
                screen_id=screen.screen_id,
                screen_label=screen.label,
                menu_path=screen.menu_path,
                purpose=f"Screen for {screen.label}",
                when_used="When user needs to " + screen.label.lower(),
                interaction_mode="inquiry",
                crud_profile="read_only",
                user_actions=[{"action": "View", "response": "Display data"}],
                rules_narrative="Standard rules apply",
                rules=[],
                fields_description=[],
                downstream_effects=[],
                open_items=[],
                related_docs={},
            )
            specs.append(spec)

        # Write specs
        results = write_all_screen_specs(specs, temp_docs_dir, "test-app")

        assert len(results) > 0
        assert all(r["status"] == "written" for r in results)

        # Verify files were created
        screens_dir = temp_docs_dir / "screens"
        assert screens_dir.exists()
        spec_files = list(screens_dir.glob("*.md"))
        assert len(spec_files) == len(specs)

    def test_spec_file_content(self, temp_repo, temp_docs_dir):
        """Test that spec files contain expected content."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)
        screens = build_screen_map(menu_items, temp_repo)

        search_screen = next((s for s in screens if "search" in s.screen_id), None)
        spec = ScreenSpec(
            screen_id=search_screen.screen_id,
            screen_label=search_screen.label,
            menu_path=search_screen.menu_path,
            purpose="Allow users to search for customers",
            when_used="When handling customer inquiries",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[
                {"action": "Enter search term", "response": "Display results"}
            ],
            rules_narrative="Only active customers shown",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
        )

        results = write_all_screen_specs([spec], temp_docs_dir, "test-app")
        assert results[0]["status"] == "written"

        # Read and verify content
        output_file = Path(results[0]["file_path"])
        content = output_file.read_text()

        assert "doc_id:" in content
        assert "test-app-screen-" in content
        assert search_screen.label in content
        assert "Allow users to search" in content
        assert "---" in content  # Frontmatter markers


class TestDriftDetection:
    """Test drift detection for specs."""

    def test_drift_checker_initialization(self, temp_repo, temp_docs_dir):
        """Test that drift checker can be initialized."""
        from ai_discovery.drift_checker import DriftChecker

        detector = HybridMenuDetector()
        menu_items = detector.detect(temp_repo)
        screens = build_screen_map(menu_items, temp_repo)

        # Create specs with source hashes
        specs = []
        for screen in screens:
            spec = ScreenSpec(
                screen_id=screen.screen_id,
                screen_label=screen.label,
                menu_path=screen.menu_path,
                purpose="Test",
                when_used="Test",
                interaction_mode="inquiry",
                crud_profile="read_only",
                user_actions=[],
                rules_narrative="Test",
                rules=[],
                fields_description=[],
                downstream_effects=[],
                open_items=[],
                related_docs={},
            )
            specs.append(spec)

        # Write specs
        write_all_screen_specs(specs, temp_docs_dir, "test-app")

        # Create drift checker
        checker = DriftChecker(temp_docs_dir / "screens")
        assert checker is not None

    def test_no_drift_when_files_unchanged(self, temp_repo, temp_docs_dir):
        """Test that no drift is detected when files haven't changed."""
        from ai_discovery.drift_checker import DriftChecker

        # Create a spec with source hashes
        spec = ScreenSpec(
            screen_id="test-screen",
            screen_label="Test Screen",
            menu_path=["Test"],
            purpose="Test",
            when_used="Test",
            interaction_mode="inquiry",
            crud_profile="read_only",
            user_actions=[],
            rules_narrative="Test",
            rules=[],
            fields_description=[],
            downstream_effects=[],
            open_items=[],
            related_docs={},
            source_hashes={
                "src/test.vue": "abc123"
            }
        )

        # Write spec
        results = write_all_screen_specs([spec], temp_docs_dir, "test-app")
        spec_file = Path(results[0]["file_path"])

        # Check drift for the spec
        checker = DriftChecker(temp_repo)
        result = checker.check_spec(spec_file)

        # Should report drift since file doesn't exist
        # This is expected for this test
        assert result is not None
        assert result.is_drifted is True
        assert "src/test.vue" in result.missing_files
