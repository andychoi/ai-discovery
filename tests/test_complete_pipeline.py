"""Complete end-to-end pipeline test: Menu detection → Backend mapping → Spec generation."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.menu_detector import HybridMenuDetector, build_screen_map
from ai_discovery.screen_mapper import ScreenMapper
from ai_discovery.ai.screen_spec_generator import ScreenSpec
from ai_discovery.generators.screen_doc_writer import write_all_screen_specs


@pytest.fixture
def complete_test_repo():
    """Create a complete repository with menus, APIs, backend, and batch jobs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # 1. Create menu.json
        (repo_path / "menu.json").write_text(
            """{
  "menu": [
    {
      "label": "Orders",
      "path": "/orders",
      "submenu": [
        {
          "label": "List",
          "path": "/orders/list",
          "component": "src/pages/Order/ListPage.vue"
        },
        {
          "label": "Create",
          "path": "/orders/create",
          "component": "src/pages/Order/CreatePage.vue"
        }
      ]
    }
  ]
}
"""
        )

        # 2. Create Vue components
        (repo_path / "src" / "pages").mkdir(parents=True)

        (repo_path / "src" / "pages" / "OrdersListPage.vue").write_text(
            """<template>
  <div class="order-list">
    <table>
      <tr v-for="order in orders">
        <td>{{ order.id }}</td>
      </tr>
    </table>
  </div>
</template>
<script>
export default {
  data() { return { orders: [] } },
  async mounted() {
    const res = await fetch(`/api/orders/list`);
    this.orders = await res.json();
  }
}
</script>
"""
        )

        (repo_path / "src" / "pages" / "OrdersCreatePage.vue").write_text(
            """<template>
  <div class="order-create">
    <form @submit="save">
      <input v-model="form.name" placeholder="Name">
      <button type="submit">Create</button>
    </form>
  </div>
</template>
<script>
export default {
  data() { return { form: {} } },
  methods: {
    async save() {
      const res = await fetch(`/api/orders`, {
        method: 'POST',
        body: JSON.stringify(this.form)
      });
      return res.json();
    }
  }
}
</script>
"""
        )

        # 3. Create Java backend
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Entity
        (repo_path / "src" / "main" / "java" / "com" / "example" / "OrderEntity.java").write_text(
            """package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "ORDERS")
public class OrderEntity {
    @Id
    private Long id;

    @Column(name = "CUSTOMER_NAME")
    private String name;

    @ManyToOne
    @JoinColumn(name = "ITEM_ID")
    private OrderItemEntity item;
}
"""
        )

        (repo_path / "src" / "main" / "java" / "com" / "example" / "OrderItemEntity.java").write_text(
            """package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "ORDER_ITEMS")
public class OrderItemEntity {
    @Id
    private Long id;
}
"""
        )

        # Service
        (repo_path / "src" / "main" / "java" / "com" / "example" / "OrderService.java").write_text(
            """package com.example.service;

import com.example.entity.OrderEntity;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

@Service
public class OrderService {
    @Autowired
    private RestTemplate restTemplate;

    public void notifyWarehouse(OrderEntity order) {
        restTemplate.post("https://warehouse.example.com/notify", order);
    }
}
"""
        )

        # Controller
        (repo_path / "src" / "main" / "java" / "com" / "example" / "OrderController.java").write_text(
            """package com.example.controller;

import com.example.entity.OrderEntity;
import com.example.service.OrderService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    @Autowired
    private OrderService orderService;

    @GetMapping("/list")
    public Object list() {
        return orderService.findAll();
    }

    @PostMapping
    public OrderEntity create(@RequestBody OrderEntity order) {
        return orderService.save(order);
    }
}
"""
        )

        # Batch job
        (repo_path / "src" / "main" / "java" / "com" / "example" / "OrderExportJobConfig.java").write_text(
            """package com.example.batch;

import org.springframework.batch.core.Job;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableBatchProcessing
public class OrderExportJobConfig {
    @Bean
    public Job orderExportJob() {
        // Exports ORDERS and ORDER_ITEMS to warehouse
        return null;
    }
}
"""
        )

        yield repo_path


class TestCompleteScreenCentricPipeline:
    """Test complete pipeline from menu detection to spec generation."""

    def test_full_pipeline_detects_screens(self, complete_test_repo):
        """Test menu detection."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(complete_test_repo)

        assert menu_items is not None
        screens = build_screen_map(menu_items, complete_test_repo)

        assert len(screens) == 2
        screen_ids = [s.screen_id for s in screens]
        assert "list" in screen_ids[0].lower() or "create" in screen_ids[0].lower()

    def test_full_pipeline_maps_backend(self, complete_test_repo):
        """Test backend mapping for all screens."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(complete_test_repo)
        screens = build_screen_map(menu_items, complete_test_repo)

        mapper = ScreenMapper(complete_test_repo)
        mappings = mapper.map_screens(screens)

        # Each screen should have mapped components
        for mapping in mappings:
            assert mapping.fe_component is not None
            assert len(mapping.fe_api_calls) > 0
            assert len(mapping.be_controllers) > 0
            assert len(mapping.be_services) > 0
            assert len(mapping.db_tables) > 0

    def test_full_pipeline_finds_batch_jobs(self, complete_test_repo):
        """Test batch job detection."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(complete_test_repo)
        screens = build_screen_map(menu_items, complete_test_repo)

        mapper = ScreenMapper(complete_test_repo)
        mappings = mapper.map_screens(screens)

        # At least one screen should have related batch jobs
        all_jobs = []
        for mapping in mappings:
            all_jobs.extend(mapping.batch_jobs)

        assert len(all_jobs) > 0

    def test_full_pipeline_finds_external_interfaces(self, complete_test_repo):
        """Test external interface detection."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(complete_test_repo)
        screens = build_screen_map(menu_items, complete_test_repo)

        mapper = ScreenMapper(complete_test_repo)
        mappings = mapper.map_screens(screens)

        # Should find external REST API calls
        all_interfaces = []
        for mapping in mappings:
            all_interfaces.extend(mapping.external_interfaces)

        assert len(all_interfaces) > 0
        assert any("warehouse" in iface.lower() or "REST" in iface for iface in all_interfaces)

    def test_full_pipeline_generates_specs(self, complete_test_repo):
        """Test spec generation for all screens."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(complete_test_repo)
        screens = build_screen_map(menu_items, complete_test_repo)

        mapper = ScreenMapper(complete_test_repo)
        mappings = mapper.map_screens(screens)

        # Create specs from mappings
        specs = []
        for mapping in mappings:
            spec = ScreenSpec(
                screen_id=mapping.screen.screen_id,
                screen_label=mapping.screen.label,
                menu_path=mapping.screen.menu_path,
                purpose=f"Manage {mapping.screen.label.lower()}",
                when_used=f"When user needs to {mapping.screen.label.lower()}",
                interaction_mode="workflow_step",
                crud_profile="manage",
                user_actions=[
                    {"action": "View list", "response": "Display items"},
                    {"action": "Create new", "response": "Save and redirect"},
                ],
                rules_narrative="Standard business rules apply",
                rules=[],
                fields_description=[],
                downstream_effects=[
                    {
                        "action": "Save order",
                        "effect": "Triggers warehouse notification batch job",
                    }
                ],
                open_items=[],
                related_docs={},
                fe_api_calls=mapping.fe_api_calls,
                be_controllers=mapping.be_controllers,
                be_services=mapping.be_services,
                db_tables=mapping.db_tables,
                batch_jobs=mapping.batch_jobs,
                external_interfaces=mapping.external_interfaces,
                source_hashes=mapping.source_hashes,
            )
            specs.append(spec)

        assert len(specs) > 0
        assert all(spec.purpose for spec in specs)
        assert all(spec.db_tables for spec in specs)

    def test_full_pipeline_writes_specs_to_disk(self, complete_test_repo):
        """Test writing specs to markdown files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Run full pipeline
            detector = HybridMenuDetector()
            menu_items = detector.detect(complete_test_repo)
            screens = build_screen_map(menu_items, complete_test_repo)

            mapper = ScreenMapper(complete_test_repo)
            mappings = mapper.map_screens(screens)

            specs = []
            for mapping in mappings:
                spec = ScreenSpec(
                    screen_id=mapping.screen.screen_id,
                    screen_label=mapping.screen.label,
                    menu_path=mapping.screen.menu_path,
                    purpose=f"Manage {mapping.screen.label}",
                    when_used="When needed",
                    interaction_mode="workflow_step",
                    crud_profile="manage",
                    user_actions=[],
                    rules_narrative="Standard rules",
                    rules=[],
                    fields_description=[],
                    downstream_effects=[],
                    open_items=[],
                    related_docs={},
                    fe_api_calls=mapping.fe_api_calls,
                    be_controllers=mapping.be_controllers,
                    be_services=mapping.be_services,
                    db_tables=mapping.db_tables,
                    batch_jobs=mapping.batch_jobs,
                    external_interfaces=mapping.external_interfaces,
                    source_hashes=mapping.source_hashes,
                )
                specs.append(spec)

            # Write specs
            results = write_all_screen_specs(specs, output_dir, "test-app")

            # Verify files created
            assert len(results) > 0
            assert all(r["status"] == "written" for r in results)

            # Verify content
            screens_dir = output_dir / "screens"
            spec_files = list(screens_dir.glob("*.md"))
            assert len(spec_files) == len(specs)

            # Check content of one spec
            content = spec_files[0].read_text()
            assert "---" in content  # Frontmatter
            assert "Purpose" in content or "purpose" in content.lower()
            assert "Downstream" in content or "downstream" in content.lower()

    def test_pipeline_creates_comprehensive_documentation(self, complete_test_repo):
        """Test that full pipeline creates comprehensive documentation."""
        detector = HybridMenuDetector()
        menu_items = detector.detect(complete_test_repo)
        screens = build_screen_map(menu_items, complete_test_repo)

        mapper = ScreenMapper(complete_test_repo)
        mappings = mapper.map_screens(screens)

        # Verify complete chain of documentation
        for mapping in mappings:
            # Has FE component
            assert mapping.fe_component

            # Has API calls
            assert any("/orders" in call.path for call in mapping.fe_api_calls)

            # Has backend components
            assert any(c.class_name == "OrderController" for c in mapping.be_controllers)
            assert any(c.class_name == "OrderService" for c in mapping.be_services)

            # Has database tables
            assert any("ORDER" in table for table in mapping.db_tables)

            # Has batch jobs
            assert any("Export" in job for job in mapping.batch_jobs)

            # Has external interfaces
            assert any("warehouse" in iface.lower() for iface in mapping.external_interfaces)

            # Has source hashes
            assert len(mapping.source_hashes) > 0
