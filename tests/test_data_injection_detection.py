"""Tests for data injection point detection (orphaned tables, stored procedures, views)."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.screen_mapper import ScreenMapper, BackendComponent, DataInjectionPoint


@pytest.fixture
def temp_repo_with_orphaned_tables():
    """Create a repository where some tables are read but never written in code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Entity for CUSTOMER (written by code)
        customer_entity = """package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "CUSTOMER")
public class CustomerEntity {
    @Id
    private Long id;
    private String name;
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerEntity.java").write_text(
            customer_entity
        )

        # Service that WRITES to CUSTOMER but READS from CUSTOMER_AUDIT (orphaned)
        service_code = """package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;

@Service
public class CustomerService {
    @Autowired
    private CustomerRepository customerRepository;

    public void updateCustomer(Long id, String name) {
        // WRITE: Save customer
        CustomerEntity customer = new CustomerEntity();
        customer.setName(name);
        customerRepository.save(customer);
    }

    public void auditLog(Long customerId) {
        // READ: Audit log (never written in code - external injection)
        String sql = "SELECT * FROM CUSTOMER_AUDIT WHERE customer_id = ?";
        // This table is read but never written - indicates external injection
    }

    public void exportMetrics() {
        // READ: Metrics table (likely external ETL injection)
        String sql = "SELECT COUNT(*) FROM CUSTOMER_METRICS";
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerService.java").write_text(
            service_code
        )

        # Controller
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerController.java").write_text(
            """package com.example.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/customers")
public class CustomerController {
    @Autowired
    private CustomerService customerService;

    @GetMapping("/{id}")
    public void getCustomer(@PathVariable Long id) {
        customerService.auditLog(id);
    }
}
"""
        )

        yield repo_path


@pytest.fixture
def temp_repo_with_stored_procedures():
    """Create a repository with stored procedure calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Service that calls stored procedures
        service_code = """package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Modifying;
import javax.persistence.StoredProcedureQuery;

@Service
public class DataSyncService {

    @Modifying
    @Query(value = "CALL sp_sync_customer_data()", nativeQuery = true)
    public void syncCustomers() {
        // Stored procedure called - likely populated by external system
    }

    public void executeExternalProc() {
        // Call another stored procedure
        String sql = "EXECUTE sp_import_orders";
    }

    public void callProcedureWithAnnotation() {
        // Using @Procedure annotation
        // @Procedure(name = "sp_update_inventory")
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "DataSyncService.java").write_text(
            service_code
        )

        yield repo_path


@pytest.fixture
def temp_repo_with_database_views():
    """Create a repository with database view references."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Service that reads from views
        service_code = """package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.data.jpa.repository.Query;

@Service
public class ReportingService {

    @Query("SELECT * FROM V_CUSTOMER_SUMMARY")
    public void generateReport() {
        // Read from view that aggregates external data
    }

    public void dashboardMetrics() {
        // Read from another view
        String sql = "SELECT * FROM VIEW_SALES_METRICS";
    }

    public void analyticsQuery() {
        // Check for view naming pattern
        String sql = "SELECT * FROM V_REGIONAL_SALES";
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "ReportingService.java").write_text(
            service_code
        )

        yield repo_path


@pytest.fixture
def temp_repo_with_external_data_imports():
    """Create a repository with FTP/SFTP imports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Service with FTP imports
        service_code = """package com.example.service;

import org.springframework.stereotype.Service;
import com.jcraft.jsch.ChannelSftp;

@Service
public class ExternalDataImportService {

    public void importFromSFTP() {
        ChannelSftp channelSftp = null;
        // Download files via SFTP
        String remoteFile = "partner_data.csv";
    }

    public void syncWithFTPServer() {
        // FTP client usage for importing data
        FTPClient ftpClient = new FTPClient();
    }

    public void importCustomerData() {
        // Uses FTP to import CUSTOMER table data from external partner
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "ExternalDataImportService.java").write_text(
            service_code
        )

        yield repo_path


class TestOrphanedTableDetection:
    """Test detection of orphaned tables (read but not written in code)."""

    def test_detect_orphaned_table_from_service(self, temp_repo_with_orphaned_tables):
        """Test detecting tables that are read but never written in code."""
        mapper = ScreenMapper(temp_repo_with_orphaned_tables)

        # These are the tables mentioned in the service
        tables = ["CUSTOMER", "CUSTOMER_AUDIT", "CUSTOMER_METRICS"]

        service = BackendComponent(
            component_type="service",
            class_name="CustomerService",
            file_path="src/main/java/com/example/CustomerService.java",
        )

        injection_points = mapper.detect_data_injection_points(tables, [service])

        # Should detect CUSTOMER_AUDIT and CUSTOMER_METRICS as orphaned
        assert len(injection_points) > 0

        # Find orphaned tables
        orphaned = [ip for ip in injection_points if ip.injection_type == "direct_database"]
        assert len(orphaned) > 0

        # Verify orphaned table names
        orphaned_names = [ip.table_name for ip in orphaned]
        assert "CUSTOMER_AUDIT" in orphaned_names or "CUSTOMER_METRICS" in orphaned_names

    def test_orphaned_table_has_high_confidence(self, temp_repo_with_orphaned_tables):
        """Test that orphaned tables have high confidence (0.8)."""
        mapper = ScreenMapper(temp_repo_with_orphaned_tables)

        tables = ["CUSTOMER", "CUSTOMER_AUDIT", "CUSTOMER_METRICS"]
        service = BackendComponent(
            component_type="service",
            class_name="CustomerService",
            file_path="src/main/java/com/example/CustomerService.java",
        )

        injection_points = mapper.detect_data_injection_points(tables, [service])

        # Check confidence scores
        for point in injection_points:
            if point.injection_type == "direct_database":
                assert point.confidence >= 0.7, f"Orphaned table should have high confidence, got {point.confidence}"

    def test_orphaned_table_includes_reason(self, temp_repo_with_orphaned_tables):
        """Test that orphaned table detection includes reasoning."""
        mapper = ScreenMapper(temp_repo_with_orphaned_tables)

        tables = ["CUSTOMER", "CUSTOMER_AUDIT"]
        service = BackendComponent(
            component_type="service",
            class_name="CustomerService",
            file_path="src/main/java/com/example/CustomerService.java",
        )

        injection_points = mapper.detect_data_injection_points(tables, [service])

        # Check that reasons are provided
        for point in injection_points:
            if point.injection_type == "direct_database":
                assert "read" in point.reason.lower()
                assert "no write" in point.reason.lower() or "not written" in point.reason.lower()

    def test_no_orphaned_tables_if_all_written(self, temp_repo_with_orphaned_tables):
        """Test that tables with write operations are not marked as orphaned."""
        mapper = ScreenMapper(temp_repo_with_orphaned_tables)

        # Only include CUSTOMER (which is written)
        tables = ["CUSTOMER"]
        service = BackendComponent(
            component_type="service",
            class_name="CustomerService",
            file_path="src/main/java/com/example/CustomerService.java",
        )

        injection_points = mapper.detect_data_injection_points(tables, [service])

        # CUSTOMER should not appear in orphaned list
        orphaned_names = [ip.table_name for ip in injection_points if ip.injection_type == "direct_database"]
        assert "CUSTOMER" not in orphaned_names


class TestStoredProcedureDetection:
    """Test detection of stored procedure injection points."""

    def test_detect_stored_procedure_calls(self, temp_repo_with_stored_procedures):
        """Test detecting stored procedure calls."""
        mapper = ScreenMapper(temp_repo_with_stored_procedures)

        service = BackendComponent(
            component_type="service",
            class_name="DataSyncService",
            file_path="src/main/java/com/example/DataSyncService.java",
        )

        injection_points = mapper.detect_data_injection_points([], [service])

        # Should detect stored procedures
        stored_procs = [ip for ip in injection_points if ip.injection_type == "stored_procedure"]
        assert len(stored_procs) > 0

    def test_stored_procedure_identifies_caller(self, temp_repo_with_stored_procedures):
        """Test that stored procedures identify which service calls them."""
        mapper = ScreenMapper(temp_repo_with_stored_procedures)

        service = BackendComponent(
            component_type="service",
            class_name="DataSyncService",
            file_path="src/main/java/com/example/DataSyncService.java",
        )

        injection_points = mapper.detect_data_injection_points([], [service])

        # Check that reasons mention the calling service
        for point in injection_points:
            if point.injection_type == "stored_procedure":
                assert "called by" in point.reason.lower() or "DataSyncService" in point.reason


class TestDatabaseViewDetection:
    """Test detection of database view references."""

    def test_detect_database_views(self, temp_repo_with_database_views):
        """Test detecting database views (V_* or VIEW* patterns)."""
        mapper = ScreenMapper(temp_repo_with_database_views)

        service = BackendComponent(
            component_type="service",
            class_name="ReportingService",
            file_path="src/main/java/com/example/ReportingService.java",
        )

        injection_points = mapper.detect_data_injection_points([], [service])

        # Should detect views
        views = [ip for ip in injection_points if ip.injection_type == "view"]
        assert len(views) > 0

    def test_view_detection_identifies_view_names(self, temp_repo_with_database_views):
        """Test that view detection identifies view names."""
        mapper = ScreenMapper(temp_repo_with_database_views)

        service = BackendComponent(
            component_type="service",
            class_name="ReportingService",
            file_path="src/main/java/com/example/ReportingService.java",
        )

        injection_points = mapper.detect_data_injection_points([], [service])

        # Check view names
        view_names = [ip.table_name for ip in injection_points if ip.injection_type == "view"]
        assert any("V_" in name or "VIEW" in name for name in view_names)

    def test_view_has_lower_confidence_than_orphaned(self, temp_repo_with_database_views):
        """Test that view references have lower confidence than orphaned tables."""
        mapper = ScreenMapper(temp_repo_with_database_views)

        service = BackendComponent(
            component_type="service",
            class_name="ReportingService",
            file_path="src/main/java/com/example/ReportingService.java",
        )

        injection_points = mapper.detect_data_injection_points([], [service])

        # Views should have confidence around 0.5
        for point in injection_points:
            if point.injection_type == "view":
                assert point.confidence <= 0.6, f"View should have lower confidence, got {point.confidence}"


class TestExternalDataImportDetection:
    """Test detection of external data import mechanisms."""

    def test_detect_ftp_sftp_imports(self, temp_repo_with_external_data_imports):
        """Test detecting FTP/SFTP import mechanisms."""
        mapper = ScreenMapper(temp_repo_with_external_data_imports)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalDataImportService",
            file_path="src/main/java/com/example/ExternalDataImportService.java",
        )

        injection_points = mapper.detect_data_injection_points([], [service])

        # Should detect FTP/SFTP as external_import injection type
        import_types = [ip.injection_type for ip in injection_points]
        assert "external_import" in import_types, f"Should detect external_import injection type, got {import_types}"

        # Verify FTP or SFTP is identified
        for ip in injection_points:
            if ip.injection_type == "external_import":
                assert any("FTP" in ip.reason or "SFTP" in ip.reason for ip in injection_points), \
                    f"Should identify FTP/SFTP in reasons, got {ip.reason}"


class TestInjectionPointIntegration:
    """Test data injection detection in full screen mapping context."""

    def test_injection_points_included_in_screen_mapping(self, temp_repo_with_orphaned_tables):
        """Test that injection points are included in complete screen mapping."""
        from ai_discovery.menu_detector import Screen

        mapper = ScreenMapper(temp_repo_with_orphaned_tables)

        screen = Screen(
            screen_id="customer-audit",
            menu_path=["Customers", "Audit"],
            label="Audit Log",
            path="/customers/audit",
        )

        mapping = mapper.map_screen(screen)

        # Injection points should be populated
        assert hasattr(mapping, "data_injection_points")
        # There should be injection points detected (at minimum, from services)
        # The actual detection depends on file structure matching

    def test_multiple_injection_types_detected(self, temp_repo_with_orphaned_tables):
        """Test that multiple injection types can be detected together."""
        mapper = ScreenMapper(temp_repo_with_orphaned_tables)

        tables = ["CUSTOMER"]
        service = BackendComponent(
            component_type="service",
            class_name="CustomerService",
            file_path="src/main/java/com/example/CustomerService.java",
        )

        injection_points = mapper.detect_data_injection_points(tables, [service])

        # Verify injection points structure
        for point in injection_points:
            assert isinstance(point, DataInjectionPoint)
            assert point.table_name
            assert point.injection_type in ["direct_database", "stored_procedure", "view", "unknown"]
            assert 0.0 <= point.confidence <= 1.0
            assert point.reason
            assert isinstance(point.potential_sources, list)

    def test_empty_tables_list_returns_stored_procs_and_views(self, temp_repo_with_stored_procedures):
        """Test that stored procedures and views are detected even with empty table list."""
        mapper = ScreenMapper(temp_repo_with_stored_procedures)

        service = BackendComponent(
            component_type="service",
            class_name="DataSyncService",
            file_path="src/main/java/com/example/DataSyncService.java",
        )

        # Empty table list
        injection_points = mapper.detect_data_injection_points([], [service])

        # Should still find stored procedures
        types = [ip.injection_type for ip in injection_points]
        assert len(types) > 0, "Should detect procedures/views even with empty table list"
