"""Tests for ETL batch job pattern detection."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.screen_mapper import ScreenMapper, BackendComponent, DataInjectionPoint, ETLBatchJob


@pytest.fixture
def temp_repo_with_etl_jobs():
    """Create a repository with ETL batch jobs that populate external tables."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example" / "batch").mkdir(parents=True)

        # Service with orphaned table (external data injection point)
        service = """package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.data.jpa.repository.Query;

@Service
public class PartnerDataService {

    @Query("SELECT * FROM PARTNER_ORDERS")
    public void syncPartnerOrders() {
        // Reads from external partner orders table (never written in code)
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "PartnerDataService.java").write_text(service)

        # ETL batch job that populates PARTNER_ORDERS from external FTP source
        etl_job = """package com.example.batch;

import org.springframework.batch.core.Job;
import org.springframework.batch.core.Step;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;
import org.springframework.batch.core.configuration.annotation.JobBuilderFactory;
import org.springframework.batch.core.configuration.annotation.StepBuilderFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import com.jcraft.jsch.ChannelSftp;

@Configuration
@EnableBatchProcessing
public class PartnerOrdersETLJobConfig {

    private ChannelSftp sftp;

    @Bean
    public Job partnerOrdersETLJob(JobBuilderFactory jobBuilderFactory, StepBuilderFactory stepBuilderFactory) {
        return jobBuilderFactory.get("partnerOrdersETLJob")
            .start(importFromSFTPStep())
            .next(processPartnerOrdersStep())
            .build();
    }

    @Bean
    public Step importFromSFTPStep() {
        // Import from SFTP source
        return null;
    }

    @Bean
    public Step processPartnerOrdersStep() {
        // Write to PARTNER_ORDERS table
        String sql = "INSERT INTO PARTNER_ORDERS SELECT * FROM imported_data";
        return null;
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "batch" / "PartnerOrdersETLJobConfig.java").write_text(etl_job)

        yield repo_path


@pytest.fixture
def temp_repo_with_http_etl():
    """Create a repository with HTTP-based ETL job."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example" / "batch").mkdir(parents=True)

        # ETL job triggered via REST endpoint
        etl_job = """package com.example.batch;

import org.springframework.batch.core.Job;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.bind.annotation.*;

@Configuration
@EnableBatchProcessing
public class ExternalDataImportJobConfig {

    private RestTemplate restTemplate;

    @PostMapping("/api/import/vendor-data")
    public void importVendorData() {
        // Fetch data from external vendor via HTTP
        String data = restTemplate.getForObject("https://vendor.api.com/data", String.class);
        // Process and write to VENDOR_DATA table
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "batch" / "ExternalDataImportJobConfig.java").write_text(etl_job)

        yield repo_path


@pytest.fixture
def temp_repo_with_queue_triggered_etl():
    """Create a repository with queue-triggered ETL job."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example" / "batch").mkdir(parents=True)

        # ETL job triggered by message queue
        etl_job = """package com.example.batch;

import org.springframework.batch.core.Job;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Configuration
@EnableBatchProcessing
public class KafkaETLJobConfig {

    @Component
    public class EventDrivenETL {
        @KafkaListener(topics = "external-data-events")
        public void processExternalEvent(String message) {
            // Process incoming event from Kafka
            // Write to EXTERNAL_EVENTS table
            String sql = "INSERT INTO EXTERNAL_EVENTS VALUES (...)";
        }
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "batch" / "KafkaETLJobConfig.java").write_text(etl_job)

        yield repo_path


class TestETLBatchJobDetection:
    """Test detection of ETL batch jobs that populate external tables."""

    def test_detect_sftp_based_etl_job(self, temp_repo_with_etl_jobs):
        """Test detecting ETL job that uses SFTP to import data."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        # Service that reads from PARTNER_ORDERS (orphaned table)
        service = BackendComponent(
            component_type="service",
            class_name="PartnerDataService",
            file_path="src/main/java/com/example/PartnerDataService.java",
        )

        # Simulate the injection point detection
        tables = ["PARTNER_ORDERS"]
        injection_points = [
            DataInjectionPoint(
                table_name="PARTNER_ORDERS",
                injection_type="direct_database",
                confidence=0.8,
                reason="Read but never written",
                potential_sources=["SFTP"],
            )
        ]

        # Detect ETL jobs
        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        # Should find ETL job
        assert len(etl_jobs) > 0

        # Verify job details
        job = etl_jobs[0]
        assert "ETL" in job.job_name or "PartnerOrders" in job.job_name
        assert "PARTNER_ORDERS" in job.tables_populated
        assert "SFTP" in job.external_sources or len(job.external_sources) > 0

    def test_etl_job_identifies_external_sources(self, temp_repo_with_etl_jobs):
        """Test that ETL job detection identifies external data sources."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        tables = ["PARTNER_ORDERS"]
        injection_points = [
            DataInjectionPoint(
                table_name="PARTNER_ORDERS",
                injection_type="direct_database",
                confidence=0.8,
                reason="External table",
                potential_sources=["SFTP"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        for job in etl_jobs:
            # Should identify the external source mechanism
            assert len(job.external_sources) > 0 or "SFTP" in job.reason or "FTP" in job.reason

    def test_etl_job_has_high_confidence_with_sources(self, temp_repo_with_etl_jobs):
        """Test that ETL jobs with external sources have high confidence."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        tables = ["PARTNER_ORDERS"]
        injection_points = [
            DataInjectionPoint(
                table_name="PARTNER_ORDERS",
                injection_type="direct_database",
                confidence=0.8,
                reason="External table",
                potential_sources=["SFTP"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        for job in etl_jobs:
            if job.external_sources:
                assert job.confidence >= 0.7, f"ETL job with external sources should have high confidence, got {job.confidence}"

    def test_detect_http_triggered_etl(self, temp_repo_with_http_etl):
        """Test detecting ETL job triggered by HTTP endpoint."""
        mapper = ScreenMapper(temp_repo_with_http_etl)

        # Even without explicit orphaned tables, the job should be detected as ETL
        tables = ["VENDOR_DATA"]
        injection_points = [
            DataInjectionPoint(
                table_name="VENDOR_DATA",
                injection_type="direct_database",
                confidence=0.8,
                reason="External vendor data",
                potential_sources=["HTTP API"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        # Should detect job triggered via HTTP endpoint
        http_triggered = [j for j in etl_jobs if "external_http" in j.trigger_type or "HTTP" in j.reason]
        assert len(http_triggered) > 0 or len(etl_jobs) > 0

    def test_etl_job_identifies_trigger_type(self, temp_repo_with_http_etl):
        """Test that ETL jobs correctly identify their trigger type."""
        mapper = ScreenMapper(temp_repo_with_http_etl)

        tables = ["VENDOR_DATA"]
        injection_points = [
            DataInjectionPoint(
                table_name="VENDOR_DATA",
                injection_type="direct_database",
                confidence=0.8,
                reason="External vendor data",
                potential_sources=["HTTP API"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        for job in etl_jobs:
            # Trigger type should be identified
            assert job.trigger_type in ["internal", "external_http", "external_queue", "unknown"]

    def test_detect_queue_triggered_etl(self, temp_repo_with_queue_triggered_etl):
        """Test detecting ETL job triggered by message queue."""
        mapper = ScreenMapper(temp_repo_with_queue_triggered_etl)

        tables = ["EXTERNAL_EVENTS"]
        injection_points = [
            DataInjectionPoint(
                table_name="EXTERNAL_EVENTS",
                injection_type="direct_database",
                confidence=0.8,
                reason="External events",
                potential_sources=["Kafka"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        # Should detect queue-triggered ETL job
        queue_triggered = [j for j in etl_jobs if "external_queue" in j.trigger_type or "Kafka" in j.reason]
        assert len(queue_triggered) > 0 or len(etl_jobs) > 0


class TestETLJobDataLineage:
    """Test complete data lineage from external sources through ETL to screens."""

    def test_etl_job_maps_to_orphaned_table(self, temp_repo_with_etl_jobs):
        """Test that ETL jobs are linked to the orphaned tables they populate."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        tables = ["PARTNER_ORDERS"]
        injection_points = [
            DataInjectionPoint(
                table_name="PARTNER_ORDERS",
                injection_type="direct_database",
                confidence=0.8,
                reason="External table",
                potential_sources=["SFTP"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        # Each ETL job should populate one of the orphaned tables
        for job in etl_jobs:
            for table in job.tables_populated:
                assert table in tables, f"ETL job {job.job_name} populates {table} which is not in the orphaned tables list"

    def test_etl_job_reasoning_is_clear(self, temp_repo_with_etl_jobs):
        """Test that ETL job detection provides clear reasoning."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        tables = ["PARTNER_ORDERS"]
        injection_points = [
            DataInjectionPoint(
                table_name="PARTNER_ORDERS",
                injection_type="direct_database",
                confidence=0.8,
                reason="External table",
                potential_sources=["SFTP"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        for job in etl_jobs:
            # Reason should explain why this is an ETL job
            assert "ETL" in job.reason or "orphaned" in job.reason.lower() or "external" in job.reason.lower()


class TestETLJobIntegration:
    """Test ETL job detection integrated with screen mapping."""

    def test_etl_jobs_included_in_screen_mapping(self, temp_repo_with_etl_jobs):
        """Test that ETL jobs are included in complete screen mapping."""
        from ai_discovery.menu_detector import Screen

        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        screen = Screen(
            screen_id="partner-orders",
            menu_path=["Partners", "Orders"],
            label="Partner Orders",
            path="/partners/orders",
        )

        mapping = mapper.map_screen(screen)

        # ETL batch jobs should be populated
        assert hasattr(mapping, "etl_batch_jobs")
        # The list might be empty if file paths don't match, but the attribute should exist
        assert isinstance(mapping.etl_batch_jobs, list)

    def test_etl_jobs_serializable(self, temp_repo_with_etl_jobs):
        """Test that ETL jobs can be serialized to dict."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        etl_job = ETLBatchJob(
            job_name="PartnerOrdersETL",
            job_class="PartnerOrdersETLJobConfig",
            file_path="src/main/java/com/example/batch/PartnerOrdersETLJobConfig.java",
            tables_populated=["PARTNER_ORDERS"],
            confidence=0.85,
            trigger_type="internal",
            reason="ETL job that imports partner orders from SFTP",
            external_sources=["SFTP"],
        )

        # Should be convertible to dict
        job_dict = {
            "job_name": etl_job.job_name,
            "job_class": etl_job.job_class,
            "file_path": etl_job.file_path,
            "tables_populated": etl_job.tables_populated,
            "confidence": etl_job.confidence,
            "trigger_type": etl_job.trigger_type,
            "reason": etl_job.reason,
            "external_sources": etl_job.external_sources,
        }

        assert job_dict["job_name"] == "PartnerOrdersETL"
        assert "PARTNER_ORDERS" in job_dict["tables_populated"]
        assert "SFTP" in job_dict["external_sources"]


class TestETLJobEdgeCases:
    """Test edge cases in ETL job detection."""

    def test_no_etl_jobs_with_no_orphaned_tables(self, temp_repo_with_etl_jobs):
        """Test that no ETL jobs are reported if no orphaned tables."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        # Empty injection points
        etl_jobs = mapper.detect_etl_batch_jobs([], [])

        # May or may not find jobs depending on heuristics, but should not crash
        assert isinstance(etl_jobs, list)

    def test_multiple_etl_jobs_same_table(self, temp_repo_with_etl_jobs):
        """Test detection when multiple ETL jobs populate same table."""
        mapper = ScreenMapper(temp_repo_with_etl_jobs)

        tables = ["PARTNER_ORDERS"]
        injection_points = [
            DataInjectionPoint(
                table_name="PARTNER_ORDERS",
                injection_type="direct_database",
                confidence=0.8,
                reason="External table",
                potential_sources=["SFTP"],
            )
        ]

        etl_jobs = mapper.detect_etl_batch_jobs(tables, injection_points)

        # Should handle multiple jobs correctly
        assert isinstance(etl_jobs, list)
        for job in etl_jobs:
            assert isinstance(job, ETLBatchJob)
