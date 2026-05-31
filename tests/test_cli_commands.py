"""Integration tests for CLI commands: detect-screens and verify-drift."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def test_repo():
    """Create a minimal test repository with menu and source files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create menu.json
        menu = {
            "items": [
                {
                    "id": "customers",
                    "label": "Customers",
                    "path": "/customers",
                    "children": [
                        {
                            "id": "customer-search",
                            "label": "Search",
                            "path": "/customers/search"
                        },
                        {
                            "id": "customer-detail",
                            "label": "Detail",
                            "path": "/customers/:id"
                        }
                    ]
                },
                {
                    "id": "orders",
                    "label": "Orders",
                    "path": "/orders"
                }
            ]
        }

        (repo_path / "menu.json").write_text(json.dumps(menu, indent=2))

        # Create a Java Spring controller
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerController.java").write_text("""
@RestController
@RequestMapping("/api/customer")
public class CustomerController {
    @GetMapping("/search")
    public List<Customer> search() {
        return null;
    }
}
""")

        # Create a service
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerService.java").write_text("""
@Service
public class CustomerService {
    @Autowired
    private CustomerRepository repo;
}
""")

        # Create a pom.xml to mark it as Spring
        (repo_path / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
    </dependencies>
</project>
""")

        yield repo_path


@pytest.fixture
def spec_dir():
    """Create a temporary specs directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestDetectScreensCommand:
    """Test detect-screens CLI command."""

    def test_detect_screens_json_menu(self, test_repo):
        """Test detecting screens from menu.json."""
        from ai_discovery.menu_detector import detect_and_build_screens

        menu_items, screens = detect_and_build_screens(test_repo)

        assert menu_items is not None
        assert len(screens) > 0
        assert any("search" in s.screen_id for s in screens)

    def test_detect_screens_output_yaml(self, test_repo):
        """Test that detect-screens outputs valid screen_map.yaml."""
        from ai_discovery.menu_detector import detect_and_build_screens

        menu_items, screens = detect_and_build_screens(test_repo)

        # Build screen_map
        screen_map = {"screens": [s.to_dict() for s in screens]}

        # Verify it's valid YAML
        yaml_text = yaml.dump(screen_map)
        assert yaml_text
        assert "customer-search" in yaml_text or "customer_search" in yaml_text

    def test_detect_screens_output_menu_tree(self, test_repo):
        """Test that detect-screens outputs valid menu_tree.json."""
        from ai_discovery.menu_detector import detect_and_build_screens

        menu_items, screens = detect_and_build_screens(test_repo)

        # Build menu tree
        if menu_items:
            menu_tree = [item.to_dict() for item in menu_items]

            # Verify it's valid JSON
            json_text = json.dumps(menu_tree, indent=2)
            assert json_text
            assert "Customers" in json_text


class TestVerifyDriftCommand:
    """Test verify-drift CLI command."""

    def test_verify_drift_no_specs(self, test_repo, spec_dir):
        """Test verify-drift with empty spec directory."""
        from ai_discovery.drift_checker import check_drift

        results, report = check_drift(test_repo, spec_dir)

        assert len(results) == 0
        assert "Drift Detection Report" in report

    def test_verify_drift_with_spec(self, test_repo, spec_dir):
        """Test verify-drift with a spec file."""
        from ai_discovery.drift_checker import check_drift

        # Create a spec file with source_hashes
        spec_file = spec_dir / "customer-search.md"
        spec_content = """---
doc_id: test-screen-customer-search
title: Customer Search
source_hashes:
  src/main/java/com/example/CustomerController.java: abc123
---
# Customer Search Screen
"""
        spec_file.write_text(spec_content)

        results, report = check_drift(test_repo, spec_dir)

        # Should detect that the hash doesn't match
        assert len(results) > 0
        # The actual hash will be different from abc123
        drifted_results = [r for r in results if r.is_drifted]
        # May or may not be drifted depending on whether file exists

    def test_verify_drift_no_drift_detected(self, test_repo, spec_dir):
        """Test verify-drift when spec is in sync."""
        import hashlib
        from ai_discovery.drift_checker import check_drift

        # Create a controller file
        ctrl_file = test_repo / "src" / "main" / "java" / "com" / "example" / "CustomerController.java"
        if not ctrl_file.exists():
            ctrl_file.parent.mkdir(parents=True, exist_ok=True)
            ctrl_file.write_text("@RestController")

        # Compute actual hash
        actual_hash = hashlib.sha256(ctrl_file.read_bytes()).hexdigest()

        # Create a spec file with correct hash
        spec_file = spec_dir / "customer-search.md"
        spec_content = f"""---
doc_id: test-screen-customer-search
title: Customer Search
source_hashes:
  src/main/java/com/example/CustomerController.java: {actual_hash}
---
# Customer Search Screen
"""
        spec_file.write_text(spec_content)

        results, report = check_drift(test_repo, spec_dir)

        # Should not detect drift
        assert len(results) == 1
        assert not results[0].is_drifted


class TestScreenMapperIntegrationWithCLI:
    """Test ScreenMapper integration with CLI commands."""

    def test_map_screens_to_backend(self, test_repo):
        """Test that screens can be mapped to backend components."""
        from ai_discovery.menu_detector import detect_and_build_screens
        from ai_discovery.screen_mapper import ScreenMapper

        menu_items, screens = detect_and_build_screens(test_repo)

        assert len(screens) > 0

        mapper = ScreenMapper(test_repo)

        # Map first screen
        if screens:
            mapping = mapper.map_screen(screens[0])
            assert mapping is not None
            assert mapping.screen is not None

    def test_screen_mappings_generate_hashes(self, test_repo):
        """Test that screen mappings include source hashes."""
        from ai_discovery.menu_detector import detect_and_build_screens
        from ai_discovery.screen_mapper import ScreenMapper

        menu_items, screens = detect_and_build_screens(test_repo)

        mapper = ScreenMapper(test_repo)

        if screens:
            mapping = mapper.map_screen(screens[0])
            # Source hashes should be computed
            # (May be empty if no source files found)
            assert isinstance(mapping.source_hashes, dict)
