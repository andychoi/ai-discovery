"""Tests for API-based data injection detection (REST endpoints returning external data)."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.screen_mapper import ScreenMapper, BackendComponent, APIInjectionPoint


@pytest.fixture
def temp_repo_with_api_injections():
    """Create a repository with REST endpoints that inject external data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Controller with endpoints that fetch external data
        controller = """package com.example.controller;

import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
import org.springframework.beans.factory.annotation.Autowired;

@RestController
@RequestMapping("/api/external")
public class ExternalDataController {

    @Autowired
    private RestTemplate restTemplate;

    @GetMapping("/partner-data")
    public Object getPartnerData() {
        // Fetch from external partner API
        String data = restTemplate.getForObject("https://partner.api.com/data", String.class);
        return data;
    }

    @PostMapping("/sync-vendor-products")
    public Object syncVendorProducts() {
        // Pull product data from vendor system
        Object vendorData = restTemplate.postForObject("https://vendor.example.com/products", null, Object.class);
        return vendorData;
    }

    @GetMapping("/external-feed")
    public Object getExternalFeed() {
        // Fetch from external data feed
        return restTemplate.getForObject("https://external-data.example.com/feed", Object.class);
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "ExternalDataController.java").write_text(
            controller
        )

        # Another controller with Feign client
        feign_controller = """package com.example.controller;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/partners")
public class PartnerIntegrationController {

    private final PartnerServiceClient partnerClient;

    @GetMapping("/search")
    public Object searchPartners(@RequestParam String query) {
        // Call external partner search service via Feign
        return partnerClient.search(query);
    }
}

@FeignClient(name = "partner-service", url = "https://partner.example.com")
interface PartnerServiceClient {
    @GetMapping("/api/search")
    Object search(@RequestParam String query);
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "PartnerIntegrationController.java").write_text(
            feign_controller
        )

        # Controller with HTTP client calls
        http_controller = """package com.example.controller;

import org.springframework.web.bind.annotation.*;
import java.net.HttpURLConnection;
import java.net.URL;

@RestController
@RequestMapping("/api/external-systems")
public class ExternalSystemsController {

    @GetMapping("/third-party-data")
    public Object getThirdPartyData() {
        // Fetch from third-party system via HTTP
        URL url = new URL("https://thirdparty.example.com/data");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        return conn.getInputStream();
    }

    @PostMapping("/vendor-sync")
    public Object syncWithVendor() {
        // External vendor sync endpoint
        return null;
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "ExternalSystemsController.java").write_text(
            http_controller
        )

        yield repo_path


class TestAPIInjectionDetection:
    """Test detection of REST endpoints that return external/injected data."""

    def test_detect_resttemplate_api_injection(self, temp_repo_with_api_injections):
        """Test detecting endpoints that use RestTemplate to fetch external data."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="ExternalDataController",
            file_path="src/main/java/com/example/ExternalDataController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        # Should detect multiple endpoints
        assert len(injections) > 0

        # Verify RestTemplate is identified
        for inj in injections:
            if "partner-data" in inj.endpoint_path or "sync-vendor" in inj.endpoint_path:
                assert "RestTemplate" in inj.external_indicator or "external" in inj.reason.lower()

    def test_api_injection_identifies_endpoint_path(self, temp_repo_with_api_injections):
        """Test that API injection detection identifies endpoint paths."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="ExternalDataController",
            file_path="src/main/java/com/example/ExternalDataController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        # Should identify paths
        paths = [inj.endpoint_path for inj in injections]
        assert any(path for path in paths), "Should identify endpoint paths"

    def test_api_injection_identifies_http_method(self, temp_repo_with_api_injections):
        """Test that API injection detection identifies HTTP methods."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="ExternalDataController",
            file_path="src/main/java/com/example/ExternalDataController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        for inj in injections:
            assert inj.http_method in ["GET", "POST", "PUT", "DELETE", "PATCH"]

    def test_detect_feign_client_injection(self, temp_repo_with_api_injections):
        """Test detecting Feign client interfaces as API injection."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="PartnerIntegrationController",
            file_path="src/main/java/com/example/PartnerIntegrationController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        # Should detect Feign-based endpoints
        feign_indicators = [inj for inj in injections if "Feign" in inj.external_indicator or "external" in inj.reason.lower()]
        assert len(feign_indicators) > 0 or len(injections) > 0

    def test_detect_http_client_injection(self, temp_repo_with_api_injections):
        """Test detecting direct HTTP client calls as API injection."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="ExternalSystemsController",
            file_path="src/main/java/com/example/ExternalSystemsController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        # Should detect HTTP client usage
        http_indicators = [inj for inj in injections if "HTTP" in inj.external_indicator or "third" in inj.reason.lower()]
        assert len(http_indicators) > 0 or len(injections) > 0

    def test_api_injection_has_confidence(self, temp_repo_with_api_injections):
        """Test that API injection points have confidence scores."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="ExternalDataController",
            file_path="src/main/java/com/example/ExternalDataController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        for inj in injections:
            assert 0.0 <= inj.confidence <= 1.0, f"Confidence should be 0-1, got {inj.confidence}"
            assert inj.confidence >= 0.7, "API injections should have high confidence"

    def test_api_injection_includes_reasoning(self, temp_repo_with_api_injections):
        """Test that API injection detection includes clear reasoning."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        controller = BackendComponent(
            component_type="controller",
            class_name="ExternalDataController",
            file_path="src/main/java/com/example/ExternalDataController.java",
        )

        injections = mapper.detect_api_injection_points([controller])

        for inj in injections:
            assert inj.reason, "Should include reasoning"
            assert "external" in inj.reason.lower() or "RestTemplate" in inj.reason, \
                f"Reasoning should explain external data, got: {inj.reason}"


class TestAPIInjectionIntegration:
    """Test API injection detection integrated with screen mapping."""

    def test_api_injections_included_in_screen_mapping(self, temp_repo_with_api_injections):
        """Test that API injection points are included in screen mapping."""
        from ai_discovery.menu_detector import Screen

        mapper = ScreenMapper(temp_repo_with_api_injections)

        screen = Screen(
            screen_id="external-data",
            menu_path=["Integration", "External Data"],
            label="External Data",
            path="/integration/external",
        )

        mapping = mapper.map_screen(screen)

        # API injection points should be populated
        assert hasattr(mapping, "api_injection_points")
        assert isinstance(mapping.api_injection_points, list)

    def test_api_injections_serializable(self, temp_repo_with_api_injections):
        """Test that API injection points can be serialized."""
        mapper = ScreenMapper(temp_repo_with_api_injections)

        api_injection = APIInjectionPoint(
            endpoint_path="/api/partner-data",
            controller_class="ExternalDataController",
            method_name="getPartnerData",
            http_method="GET",
            file_path="src/main/java/com/example/ExternalDataController.java",
            external_indicator="RestTemplate call to https://partner.api.com",
            confidence=0.85,
            reason="REST endpoint fetches data from external partner API via RestTemplate"
        )

        # Should be serializable
        api_dict = {
            "endpoint_path": api_injection.endpoint_path,
            "controller_class": api_injection.controller_class,
            "method_name": api_injection.method_name,
            "http_method": api_injection.http_method,
            "external_indicator": api_injection.external_indicator,
            "confidence": api_injection.confidence,
        }

        assert api_dict["endpoint_path"] == "/api/partner-data"
        assert api_dict["http_method"] == "GET"
        assert "partner" in api_dict["endpoint_path"].lower()


class TestAPIInjectionEdgeCases:
    """Test edge cases in API injection detection."""

    def test_no_api_injections_with_empty_controllers(self):
        """Test that empty controller list returns no injections."""
        mapper = ScreenMapper(Path("/"))

        injections = mapper.detect_api_injection_points([])

        assert len(injections) == 0

    def test_api_injection_dataclass_defaults(self):
        """Test that APIInjectionPoint dataclass has sensible defaults."""
        injection = APIInjectionPoint(
            endpoint_path="/api/test",
            controller_class="TestController",
            method_name="testMethod",
            http_method="GET",
            file_path="test.java",
        )

        assert injection.confidence == 0.7
        assert injection.tables_written == []
        assert injection.reason == ""
        assert injection.external_indicator == ""
