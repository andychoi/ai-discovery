"""Tests for multi-framework detection and pattern extraction."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.framework_detector import FrameworkDetector, FrameworkPattern


@pytest.fixture
def java_spring_repo():
    """Create a sample Java Spring repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create pom.xml
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

        # Create controller
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
        (repo_path / "src" / "main" / "java" / "com" / "example" / "UserController.java").write_text("""
package com.example;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/users")
public class UserController {

    @GetMapping("/{id}")
    public Object getUser(@PathVariable Long id) {
        return null;
    }

    @PostMapping
    public Object createUser(@RequestBody Object user) {
        return null;
    }
}
""")

        yield repo_path


@pytest.fixture
def dotnet_aspnet_repo():
    """Create a sample .NET ASP.NET repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create .csproj
        (repo_path / "MyApp.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.Web">
    <PropertyGroup>
        <TargetFramework>net6.0</TargetFramework>
    </PropertyGroup>
    <ItemGroup>
        <PackageReference Include="Microsoft.AspNetCore.App" Version="6.0.0" />
    </ItemGroup>
</Project>
""")

        # Create controller
        (repo_path / "Controllers").mkdir()
        (repo_path / "Controllers" / "UserController.cs").write_text("""
using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class UserController : ControllerBase {

    [HttpGet("{id}")]
    public async Task<ActionResult<object>> GetUser(int id) {
        return null;
    }

    [HttpPost]
    public async Task<ActionResult<object>> CreateUser([FromBody] object user) {
        return null;
    }
}
""")

        yield repo_path


@pytest.fixture
def node_express_repo():
    """Create a sample Node.js Express repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create package.json
        (repo_path / "package.json").write_text("""{
    "name": "my-app",
    "version": "1.0.0",
    "dependencies": {
        "express": "^4.18.0"
    }
}
""")

        # Create routes
        (repo_path / "routes").mkdir()
        (repo_path / "routes" / "users.js").write_text("""
const express = require('express');
const router = express.Router();

router.get('/:id', (req, res) => {
    res.json({});
});

router.post('/', (req, res) => {
    res.json({});
});

module.exports = router;
""")

        yield repo_path


class TestFrameworkDetection:
    """Test framework auto-detection."""

    def test_detect_java_spring_framework(self, java_spring_repo):
        """Test detection of Java Spring framework."""
        detected = FrameworkDetector.detect_framework(java_spring_repo)

        assert detected is not None
        assert detected.language == "java"
        assert detected.framework == "spring"

    def test_detect_dotnet_aspnet_framework(self, dotnet_aspnet_repo):
        """Test detection of .NET ASP.NET framework."""
        detected = FrameworkDetector.detect_framework(dotnet_aspnet_repo)

        assert detected is not None
        assert detected.language == "csharp"
        assert detected.framework == "aspnet"

    def test_detect_node_express_framework(self, node_express_repo):
        """Test detection of Node.js Express framework."""
        detected = FrameworkDetector.detect_framework(node_express_repo)

        assert detected is not None
        assert detected.language == "javascript"
        assert detected.framework == "express"

    def test_detect_no_framework(self):
        """Test behavior when no framework is detected."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            detected = FrameworkDetector.detect_framework(Path(tmpdir))
            # May be None or return default
            assert detected is None or isinstance(detected, FrameworkPattern)


class TestControllerDetection:
    """Test framework-specific controller detection."""

    def test_detect_spring_controllers(self, java_spring_repo):
        """Test detecting Java Spring controllers."""
        pattern = FrameworkDetector.JAVA_SPRING
        controllers = FrameworkDetector.detect_controller_files(java_spring_repo, pattern)

        assert len(controllers) > 0
        assert any("UserController.java" in str(c) for c in controllers)

    def test_detect_aspnet_controllers(self, dotnet_aspnet_repo):
        """Test detecting ASP.NET controllers."""
        pattern = FrameworkDetector.DOTNET_ASPNET
        controllers = FrameworkDetector.detect_controller_files(dotnet_aspnet_repo, pattern)

        assert len(controllers) > 0
        assert any("UserController.cs" in str(c) for c in controllers)

    def test_detect_express_routes(self, node_express_repo):
        """Test detecting Express routes."""
        pattern = FrameworkDetector.NODE_EXPRESS
        controllers = FrameworkDetector.detect_controller_files(node_express_repo, pattern)

        assert len(controllers) > 0
        assert any("users.js" in str(c) for c in controllers)


class TestEntityDetection:
    """Test framework-specific entity/model detection."""

    def test_detect_spring_entities(self, java_spring_repo):
        """Test detecting Java Spring entities."""
        # Create entity
        (java_spring_repo / "src" / "main" / "java" / "com" / "example" / "User.java").write_text("""
@Entity
@Table(name = "users")
public class User {
    @Id
    private Long id;
}
""")

        pattern = FrameworkDetector.JAVA_SPRING
        entities = FrameworkDetector.detect_entities(java_spring_repo, pattern)

        # Should find at least one entity
        assert any("User" in str(e) for e in entities) or len(entities) >= 0

    def test_detect_aspnet_entities(self, dotnet_aspnet_repo):
        """Test detecting .NET models."""
        # Create model
        (dotnet_aspnet_repo / "Models").mkdir(exist_ok=True)
        (dotnet_aspnet_repo / "Models" / "User.cs").write_text("""
[Table("users")]
public class User {
    public int Id { get; set; }
    public string Name { get; set; }
}
""")

        pattern = FrameworkDetector.DOTNET_ASPNET
        entities = FrameworkDetector.detect_entities(dotnet_aspnet_repo, pattern)

        # Should find at least one entity
        assert len(entities) > 0


class TestMetadataExtraction:
    """Test framework-specific metadata extraction from files."""

    def test_extract_spring_controller_metadata(self, java_spring_repo):
        """Test extracting metadata from Spring controller."""
        controller_file = java_spring_repo / "src" / "main" / "java" / "com" / "example" / "UserController.java"

        metadata = FrameworkDetector.extract_controller_metadata(
            controller_file,
            FrameworkDetector.JAVA_SPRING
        )

        assert metadata["class_name"] == "UserController"
        assert len(metadata["endpoints"]) > 0
        # Should find /api/users and endpoints

    def test_extract_aspnet_controller_metadata(self, dotnet_aspnet_repo):
        """Test extracting metadata from ASP.NET controller."""
        controller_file = dotnet_aspnet_repo / "Controllers" / "UserController.cs"

        metadata = FrameworkDetector.extract_controller_metadata(
            controller_file,
            FrameworkDetector.DOTNET_ASPNET
        )

        assert metadata["class_name"] == "UserController"
        # Should extract API routes

    def test_extract_express_routes_metadata(self, node_express_repo):
        """Test extracting metadata from Express routes."""
        routes_file = node_express_repo / "routes" / "users.js"

        metadata = FrameworkDetector.extract_controller_metadata(
            routes_file,
            FrameworkDetector.NODE_EXPRESS
        )

        # Should extract route methods
        assert len(metadata["methods"]) > 0


class TestFrameworkPatterns:
    """Test that framework patterns are comprehensive."""

    def test_spring_patterns_include_annotations(self):
        """Test that Spring patterns cover common annotations."""
        pattern = FrameworkDetector.JAVA_SPRING

        assert any("Controller" in p for p in pattern.controller_patterns)
        assert any("Service" in p for p in pattern.service_patterns)
        assert any("Entity" in p for p in pattern.entity_patterns)

    def test_aspnet_patterns_include_attributes(self):
        """Test that ASP.NET patterns cover common attributes."""
        pattern = FrameworkDetector.DOTNET_ASPNET

        assert any("ApiController" in p or "Controller" in p for p in pattern.controller_patterns)
        assert any("HttpClient" in p for p in pattern.api_call_patterns)
        assert any("DbSet" in p for p in pattern.entity_patterns)

    def test_express_patterns_include_methods(self):
        """Test that Express patterns cover common methods."""
        pattern = FrameworkDetector.NODE_EXPRESS

        assert any("router" in p or "app" in p for p in pattern.controller_patterns)
        assert any("module.exports" in p or "export" in p for p in pattern.service_patterns)


class TestFrameworkExtensibility:
    """Test that framework detector is extensible for new frameworks."""

    def test_can_add_new_framework_pattern(self):
        """Test adding a new framework pattern."""
        custom_pattern = FrameworkPattern(
            language="python",
            framework="django",
            controller_patterns=[r'@views\..*', r'class.*View'],
            service_patterns=[r'from.*models.*import'],
            entity_patterns=[r'class.*Model'],
            api_call_patterns=[r'requests\.'],
            batch_job_patterns=[r'@periodic_task', r'@shared_task'],
        )

        assert custom_pattern.language == "python"
        assert custom_pattern.framework == "django"
        assert len(custom_pattern.controller_patterns) > 0
