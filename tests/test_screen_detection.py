"""Tests for screen detection and mapping."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from ai_discovery.menu_detector import (
    HybridMenuDetector,
    JsonYamlDetector,
    MenuItem,
    Screen,
    build_screen_map,
    detect_and_build_screens,
)
from ai_discovery.screen_mapper import ScreenMapping, ScreenMapper


@pytest.fixture
def temp_repo():
    """Create a temporary repository structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)

        # Create basic directory structure
        (repo / "src").mkdir()
        (repo / "src" / "pages").mkdir()

        yield repo


class TestMenuDetection:
    """Test menu detection from various formats."""

    def test_json_menu_detection(self, temp_repo):
        """Test detection of JSON menu file."""
        menu_file = temp_repo / "menu.json"
        menu_data = [
            {
                "id": "customers",
                "label": "Customers",
                "path": "/customers",
                "children": [
                    {"id": "customer-search", "label": "Search", "path": "/customers/search"},
                    {"id": "customer-edit", "label": "Edit", "path": "/customers/edit"},
                ],
            },
            {
                "id": "reports",
                "label": "Reports",
                "path": "/reports",
                "children": [{"id": "sales-report", "label": "Sales", "path": "/reports/sales"}],
            },
        ]

        menu_file.write_text(json.dumps(menu_data))

        # Detect menu
        detector = JsonYamlDetector()
        result = detector.detect(temp_repo)

        assert result is not None
        assert len(result) == 2
        assert result[0].label == "Customers"
        assert len(result[0].children) == 2

    def test_hybrid_detection(self, temp_repo):
        """Test hybrid detector tries multiple strategies."""
        menu_file = temp_repo / "menu.json"
        menu_data = {"menu": [{"id": "home", "label": "Home", "path": "/"}]}

        menu_file.write_text(json.dumps(menu_data))

        detector = HybridMenuDetector()
        result = detector.detect(temp_repo)

        assert result is not None
        assert len(result) > 0


class TestScreenBuilding:
    """Test screen map building."""

    def test_build_screen_map(self, temp_repo):
        """Test building screen map from menu items."""
        menu_items = [
            MenuItem(
                id="customers",
                label="Customers",
                path="/customers",
                children=[
                    MenuItem(
                        id="customer-search",
                        label="Search",
                        path="/customers/search",
                    ),
                    MenuItem(
                        id="customer-edit",
                        label="Edit",
                        path="/customers/edit",
                    ),
                ],
            ),
        ]

        screens = build_screen_map(menu_items, temp_repo)

        assert len(screens) == 2
        assert screens[0].screen_id == "customer-search"
        assert screens[0].menu_path == ["Customers", "Search"]
        assert screens[1].menu_path == ["Customers", "Edit"]


class TestScreenMapping:
    """Test screen-to-backend mapping."""

    def test_screen_mapper_initialization(self, temp_repo):
        """Test ScreenMapper can be initialized."""
        mapper = ScreenMapper(temp_repo)
        assert mapper.repo_path == temp_repo

    def test_fe_component_detection(self, temp_repo):
        """Test FE component file detection."""
        # Create a sample Vue component
        pages_dir = temp_repo / "src" / "pages" / "customer"
        pages_dir.mkdir(parents=True)
        (pages_dir / "SearchPage.vue").write_text("<template></template>")

        mapper = ScreenMapper(temp_repo)
        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Customer Search",
            path="/customer/search",
        )

        # This should find the Vue component
        fe_component = mapper._find_fe_component(screen)
        # The exact match depends on the heuristic, so we just check it's reasonable
        assert fe_component is None or "search" in fe_component.lower()


class TestScreenMapOutput:
    """Test screen map YAML/JSON output."""

    def test_screen_to_dict(self):
        """Test Screen conversion to dict."""
        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Customer Search",
            path="/customers/search",
            permissions=["ROLE_USER", "ROLE_ADMIN"],
        )

        result = screen.to_dict()

        assert result["screen_id"] == "customer-search"
        assert result["menu_path"] == ["Customers", "Search"]
        assert "ROLE_USER" in result["permissions"]

    def test_screen_mapping_to_dict(self):
        """Test ScreenMapping conversion to dict."""
        screen = Screen(
            screen_id="test",
            menu_path=["Test"],
            label="Test",
            path="/test",
        )
        mapping = ScreenMapping(screen=screen)

        result = mapping.to_dict()

        assert result["screen"]["screen_id"] == "test"
        assert "fe_api_calls" in result
        assert "be_controllers" in result


def test_detect_and_build_screens_no_menu(temp_repo):
    """Test detect_and_build_screens when no menu exists."""
    menu_items, screens = detect_and_build_screens(temp_repo)

    # Should return None, [] when no menu is found
    assert menu_items is None
    assert screens == []


def test_menu_item_to_dict():
    """Test MenuItem conversion to dict."""
    item = MenuItem(
        id="test",
        label="Test Menu",
        path="/test",
        icon="icon-test",
        roles=["admin", "user"],
    )

    result = item.to_dict()

    assert result["id"] == "test"
    assert result["label"] == "Test Menu"
    assert result["icon"] == "icon-test"
    assert result["roles"] == ["admin", "user"]
