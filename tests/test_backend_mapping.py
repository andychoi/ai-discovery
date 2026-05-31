"""Tests for backend mapping and controller resolution."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.menu_detector import Screen
from ai_discovery.screen_mapper import ScreenMapper, ApiCall


@pytest.fixture
def temp_java_repo():
    """Create a temporary Java Spring repository."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create directory structure
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Create a simple entity
        entity_code = """package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "CUSTOMER")
public class CustomerEntity {
    @Id
    private Long id;

    @Column(name = "NAME")
    private String name;

    @Column(name = "EMAIL")
    private String email;
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerEntity.java").write_text(
            entity_code
        )

        # Create a service
        service_code = """package com.example.service;

import com.example.entity.CustomerEntity;
import com.example.repository.CustomerRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class CustomerService {
    @Autowired
    private CustomerRepository repository;

    public CustomerEntity findById(Long id) {
        return repository.findById(id).orElse(null);
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerService.java").write_text(
            service_code
        )

        # Create a controller
        controller_code = """package com.example.controller;

import com.example.entity.CustomerEntity;
import com.example.service.CustomerService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/customers")
public class CustomerController {
    @Autowired
    private CustomerService customerService;

    @GetMapping("/search")
    public Object search(@RequestParam String q) {
        return customerService.search(q);
    }

    @GetMapping("/{id}")
    public CustomerEntity getById(@PathVariable Long id) {
        return customerService.findById(id);
    }

    @PostMapping
    public CustomerEntity create(@RequestBody CustomerEntity customer) {
        return customerService.save(customer);
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerController.java").write_text(
            controller_code
        )

        yield repo_path


class TestControllerResolution:
    """Test resolving API calls to controllers."""

    def test_resolve_exact_path(self, temp_java_repo):
        """Test resolving API call with exact path match."""
        mapper = ScreenMapper(temp_java_repo)

        api_call = ApiCall(method="GET", path="/api/customers/search")
        controllers = mapper._resolve_to_controller(api_call)

        assert len(controllers) > 0
        assert controllers[0].class_name == "CustomerController"

    def test_resolve_path_variable(self, temp_java_repo):
        """Test resolving API call with path variables."""
        mapper = ScreenMapper(temp_java_repo)

        api_call = ApiCall(method="GET", path="/api/customers/123")
        controllers = mapper._resolve_to_controller(api_call)

        assert len(controllers) > 0
        assert controllers[0].class_name == "CustomerController"

    def test_resolve_post_endpoint(self, temp_java_repo):
        """Test resolving POST endpoint."""
        mapper = ScreenMapper(temp_java_repo)

        api_call = ApiCall(method="POST", path="/api/customers")
        controllers = mapper._resolve_to_controller(api_call)

        assert len(controllers) > 0
        assert controllers[0].class_name == "CustomerController"

    def test_extract_class_mapping(self, temp_java_repo):
        """Test extracting class-level @RequestMapping."""
        mapper = ScreenMapper(temp_java_repo)

        controller_file = (
            temp_java_repo / "src" / "main" / "java" / "com" / "example" / "CustomerController.java"
        )
        content = controller_file.read_text()

        class_mapping = mapper._extract_class_mapping(content)
        assert class_mapping == "/api/customers"

    def test_extract_method_mappings(self, temp_java_repo):
        """Test extracting method-level mappings."""
        mapper = ScreenMapper(temp_java_repo)

        controller_file = (
            temp_java_repo / "src" / "main" / "java" / "com" / "example" / "CustomerController.java"
        )
        content = controller_file.read_text()

        get_mappings = mapper._extract_method_mappings(content, "GET")
        assert "/search" in get_mappings
        assert "/{id}" in get_mappings

        post_mappings = mapper._extract_method_mappings(content, "POST")
        assert len(post_mappings) > 0


class TestServiceResolution:
    """Test resolving controllers to services."""

    def test_resolve_autowired_service(self, temp_java_repo):
        """Test finding @Autowired services in controller."""
        mapper = ScreenMapper(temp_java_repo)
        from ai_discovery.screen_mapper import BackendComponent

        controller = BackendComponent(
            component_type="controller",
            class_name="CustomerController",
            file_path="src/main/java/com/example/CustomerController.java",
        )

        services = mapper._resolve_to_services(controller)

        assert len(services) > 0
        assert services[0].class_name == "CustomerService"
        assert services[0].component_type == "service"


class TestDatabaseTableExtraction:
    """Test extracting database tables."""

    def test_extract_table_from_entity(self, temp_java_repo):
        """Test extracting table name from @Table annotation."""
        mapper = ScreenMapper(temp_java_repo)

        entity_file = (
            temp_java_repo / "src" / "main" / "java" / "com" / "example" / "CustomerEntity.java"
        )
        content = entity_file.read_text()

        # Since _extract_db_tables expects services, we'll create a minimal service
        from ai_discovery.screen_mapper import BackendComponent

        service = BackendComponent(
            component_type="service",
            class_name="CustomerService",
            file_path="src/main/java/com/example/CustomerService.java",
        )

        tables = mapper._extract_db_tables([service])

        assert len(tables) > 0
        assert "CUSTOMER" in tables

    def test_full_screen_mapping(self, temp_java_repo):
        """Test full screen mapping workflow."""
        # Create a Vue component that calls the API
        (temp_java_repo / "src" / "pages").mkdir(parents=True, exist_ok=True)
        (temp_java_repo / "src" / "pages" / "CustomersSearchPage.vue").write_text(
            """<template>
  <div>
    <input v-model="q" @change="search">
  </div>
</template>
<script>
export default {
  data() { return { q: '' } },
  methods: {
    async search() {
      const res = await fetch(`/api/customers/search?q=${this.q}`);
      return res.json();
    }
  }
}
</script>
"""
        )

        mapper = ScreenMapper(temp_java_repo)

        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Search",
            path="/customers/search",
        )

        mapping = mapper.map_screen(screen)

        # Verify the full chain was mapped
        assert mapping.fe_component is not None
        assert len(mapping.fe_api_calls) > 0
        assert len(mapping.be_controllers) > 0
        assert len(mapping.be_services) > 0
        assert len(mapping.db_tables) > 0
        assert "CUSTOMER" in mapping.db_tables


class TestPathMatching:
    """Test path matching logic."""

    def test_exact_path_match(self):
        """Test exact path matching."""
        mapper = ScreenMapper(Path("/"))

        assert mapper._path_matches("/api/customers", "/api/customers")
        assert mapper._path_matches("/api/customers", "/api/customers/")
        assert not mapper._path_matches("/api/customers", "/api/orders")

    def test_path_variable_match(self):
        """Test matching paths with variables."""
        mapper = ScreenMapper(Path("/"))

        assert mapper._path_matches("/api/customers/123", "/api/customers/{id}")
        assert mapper._path_matches("/api/customers/456", "/api/customers/{id}")
        assert not mapper._path_matches("/api/customers/123/detail", "/api/customers/{id}")
