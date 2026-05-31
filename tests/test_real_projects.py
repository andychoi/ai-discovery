"""Real-world project validation tests for screen-centric pipeline."""

import json
import tempfile
from pathlib import Path
from typing import Tuple

import pytest
import yaml


def get_fixture_project(name: str) -> Path:
    """Get path to a committed fixture project."""
    project_path = Path(__file__).parent / "fixtures" / "projects" / name
    assert project_path.exists(), f"Fixture project not found: {project_path}"
    return project_path


@pytest.fixture
def spring_project():
    """Fixture providing a Spring Boot test project."""
    return get_fixture_project("spring-boot-app")


@pytest.fixture
def aspnet_project():
    """Fixture providing an ASP.NET Core test project."""
    return get_fixture_project("aspnet-core-app")


@pytest.fixture
def express_project():
    """Fixture providing an Express.js test project."""
    return get_fixture_project("express-app")


class TestSpringBootValidation:
    """Validate pipeline on Spring Boot project."""

    def test_detect_screens_spring_boot(self, spring_project):
        """Test screen detection on Spring Boot project."""
        from ai_discovery.menu_detector import detect_and_build_screens

        menu_items, screens = detect_and_build_screens(spring_project)

        assert len(screens) > 0
        assert any("products" in s.screen_id for s in screens)
        assert any("orders" in s.screen_id for s in screens)

    def test_map_screens_to_backend_spring(self, spring_project):
        """Test backend mapping on Spring Boot project."""
        from ai_discovery.menu_detector import detect_and_build_screens
        from ai_discovery.screen_mapper import ScreenMapper

        menu_items, screens = detect_and_build_screens(spring_project)
        mapper = ScreenMapper(spring_project)

        # Verify mapping works without errors
        mappings = []
        for screen in screens:
            try:
                mapping = mapper.map_screen(screen)
                mappings.append(mapping)
            except Exception as e:
                pytest.fail(f"Failed to map screen {screen.screen_id}: {e}")

        # Should have created mappings for all screens
        assert len(mappings) == len(screens)

    def test_spring_framework_detection(self, spring_project):
        """Test that Spring framework is correctly detected."""
        from ai_discovery.framework_detector import FrameworkDetector

        detected = FrameworkDetector.detect_framework(spring_project)

        assert detected is not None
        assert detected.framework == "spring"
        assert detected.language == "java"


class TestAspNetCoreValidation:
    """Validate pipeline on ASP.NET Core project."""

    def test_detect_screens_aspnet(self, aspnet_project):
        """Test screen detection on ASP.NET project."""
        from ai_discovery.menu_detector import detect_and_build_screens

        menu_items, screens = detect_and_build_screens(aspnet_project)

        assert len(screens) > 0
        assert any("products" in s.screen_id for s in screens)

    def test_aspnet_framework_detection(self, aspnet_project):
        """Test that ASP.NET framework is correctly detected."""
        from ai_discovery.framework_detector import FrameworkDetector

        detected = FrameworkDetector.detect_framework(aspnet_project)

        assert detected is not None
        assert detected.framework == "aspnet"
        assert detected.language == "csharp"


class TestExpressValidation:
    """Validate pipeline on Express.js project."""

    def test_detect_screens_express(self, express_project):
        """Test screen detection on Express project."""
        from ai_discovery.menu_detector import detect_and_build_screens

        menu_items, screens = detect_and_build_screens(express_project)

        assert len(screens) > 0
        assert any("products" in s.screen_id for s in screens)

    def test_map_screens_express(self, express_project):
        """Test backend mapping on Express project."""
        from ai_discovery.menu_detector import detect_and_build_screens
        from ai_discovery.screen_mapper import ScreenMapper

        menu_items, screens = detect_and_build_screens(express_project)
        mapper = ScreenMapper(express_project)

        mappings = [mapper.map_screen(s) for s in screens if s]

        assert len(mappings) > 0

    def test_express_framework_detection(self, express_project):
        """Test that Express framework is correctly detected."""
        from ai_discovery.framework_detector import FrameworkDetector

        detected = FrameworkDetector.detect_framework(express_project)

        assert detected is not None
        assert detected.framework == "express"
        assert detected.language == "javascript"


class TestCrossFrameworkComparison:
    """Compare pipeline behavior across frameworks."""

    def test_all_frameworks_detect_screens(self, spring_project, aspnet_project, express_project):
        """Verify all frameworks successfully detect screens."""
        from ai_discovery.menu_detector import detect_and_build_screens

        spring_items, spring_screens = detect_and_build_screens(spring_project)
        aspnet_items, aspnet_screens = detect_and_build_screens(aspnet_project)
        express_items, express_screens = detect_and_build_screens(express_project)

        assert len(spring_screens) > 0
        assert len(aspnet_screens) > 0
        assert len(express_screens) > 0

        # All should detect similar menu structures
        assert len(spring_screens) >= 8  # 4 menu items with children
        assert len(aspnet_screens) >= 4  # 2 menu items with children
        assert len(express_screens) >= 5  # 2 menu items with children

    def test_framework_detection_consistent(self, spring_project, aspnet_project, express_project):
        """Verify framework detection is correct for all projects."""
        from ai_discovery.framework_detector import FrameworkDetector

        spring = FrameworkDetector.detect_framework(spring_project)
        aspnet = FrameworkDetector.detect_framework(aspnet_project)
        express = FrameworkDetector.detect_framework(express_project)

        assert spring.framework == "spring"
        assert aspnet.framework == "aspnet"
        assert express.framework == "express"
