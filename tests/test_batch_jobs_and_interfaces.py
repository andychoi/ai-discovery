"""Tests for batch job and external interface detection."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.screen_mapper import ScreenMapper, BackendComponent


@pytest.fixture
def temp_repo_with_batch_jobs():
    """Create a temporary repository with batch jobs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Create a Spring Batch job configuration
        batch_config = """package com.example.batch;

import org.springframework.batch.core.Job;
import org.springframework.batch.core.Step;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;
import org.springframework.batch.core.configuration.annotation.JobBuilderFactory;
import org.springframework.batch.core.configuration.annotation.StepBuilderFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableBatchProcessing
public class CustomerExportJobConfig {

    @Autowired
    private JobBuilderFactory jobBuilderFactory;

    @Autowired
    private StepBuilderFactory stepBuilderFactory;

    @Bean
    public Job customerExportJob() {
        return jobBuilderFactory.get("customerExportJob")
            .start(exportStep())
            .build();
    }

    @Bean
    public Step exportStep() {
        return stepBuilderFactory.get("exportStep")
            .<CustomerEntity, CustomerExportDto>chunk(100)
            .reader(customerReader())
            .processor(customerProcessor())
            .writer(customerWriter())
            .build();
    }

    // Reader, processor, writer beans...
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "CustomerExportJobConfig.java").write_text(
            batch_config
        )

        # Create customer entity
        entity_code = """package com.example.entity;

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
            entity_code
        )

        # Create a scheduled job
        scheduled_job = """package com.example.batch;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class DailyProcessorJob {

    @Scheduled(cron = "0 0 2 * * *")
    public void processDaily() {
        // Process CUSTOMER and CUSTOMER_ORDER tables daily
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "DailyProcessorJob.java").write_text(
            scheduled_job
        )

        yield repo_path


@pytest.fixture
def temp_repo_with_external_apis():
    """Create a temporary repository with external API calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Create a service with external API calls
        service_code = """package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.jms.core.JmsTemplate;
import com.amazonaws.services.s3.AmazonS3;
import java.net.URL;

@Service
public class ExternalIntegrationService {

    private RestTemplate restTemplate;
    private PaymentServiceClient paymentClient;
    private KafkaTemplate<String, String> kafkaTemplate;
    private JmsTemplate jmsTemplate;
    private AmazonS3 s3Client;

    public void processPayment() {
        // Call external payment API
        restTemplate.post("https://api.paymentgateway.com/process", request);
    }

    public void callExternalService() {
        // Feign client call
        paymentClient.charge(payment);
    }

    public void publishEvent() {
        // Kafka
        kafkaTemplate.send("customer-events", message);
        // JMS
        jmsTemplate.convertAndSend("customer-queue", message);
    }

    public void uploadToS3() {
        // AWS S3
        s3Client.putObject("bucket-name", "key", "value");
    }

    public void makeHttpCall() {
        URL url = new URL("https://external-api.example.com/endpoint");
    }
}

@FeignClient(value = "payment-service", url = "https://api.paymentgateway.com")
interface PaymentServiceClient {
    void charge(Payment payment);
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "ExternalIntegrationService.java").write_text(
            service_code
        )

        yield repo_path


class TestBatchJobDetection:
    """Test finding batch jobs that access the same tables."""

    def test_find_batch_job_by_table_reference(self, temp_repo_with_batch_jobs):
        """Test detecting batch job that references a table."""
        mapper = ScreenMapper(temp_repo_with_batch_jobs)

        # Tables accessed by the screen's service
        tables = ["CUSTOMER", "CUSTOMER_ORDER"]

        jobs = mapper._find_related_batch_jobs(tables)

        assert len(jobs) > 0
        # Should find CustomerExportJob
        assert any("Export" in job or "Processor" in job for job in jobs)

    def test_no_batch_jobs_if_empty_tables(self, temp_repo_with_batch_jobs):
        """Test that empty table list returns no jobs."""
        mapper = ScreenMapper(temp_repo_with_batch_jobs)

        jobs = mapper._find_related_batch_jobs([])
        assert len(jobs) == 0

    def test_find_scheduled_job(self, temp_repo_with_batch_jobs):
        """Test detecting @Scheduled jobs."""
        mapper = ScreenMapper(temp_repo_with_batch_jobs)

        tables = ["CUSTOMER"]
        jobs = mapper._find_related_batch_jobs(tables)

        # Should find DailyProcessor job
        assert len(jobs) > 0
        assert any("Daily" in job or "Processor" in job for job in jobs)


class TestExternalInterfaceDetection:
    """Test finding external system interfaces."""

    def test_detect_rest_template_calls(self, temp_repo_with_external_apis):
        """Test detecting RestTemplate API calls."""
        mapper = ScreenMapper(temp_repo_with_external_apis)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalIntegrationService",
            file_path="src/main/java/com/example/ExternalIntegrationService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find REST API calls
        assert any("REST" in iface for iface in interfaces)
        assert any("payment" in iface.lower() for iface in interfaces)

    def test_detect_feign_client(self, temp_repo_with_external_apis):
        """Test detecting @FeignClient annotations."""
        mapper = ScreenMapper(temp_repo_with_external_apis)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalIntegrationService",
            file_path="src/main/java/com/example/ExternalIntegrationService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find Feign client
        assert any("FeignClient" in iface or "payment-service" in iface.lower() for iface in interfaces)

    def test_detect_kafka_usage(self, temp_repo_with_external_apis):
        """Test detecting Kafka message broker usage."""
        mapper = ScreenMapper(temp_repo_with_external_apis)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalIntegrationService",
            file_path="src/main/java/com/example/ExternalIntegrationService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find Kafka
        assert any("Kafka" in iface for iface in interfaces)

    def test_detect_jms_usage(self, temp_repo_with_external_apis):
        """Test detecting JMS message queue usage."""
        mapper = ScreenMapper(temp_repo_with_external_apis)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalIntegrationService",
            file_path="src/main/java/com/example/ExternalIntegrationService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find JMS
        assert any("JMS" in iface for iface in interfaces)

    def test_detect_aws_s3(self, temp_repo_with_external_apis):
        """Test detecting AWS S3 usage."""
        mapper = ScreenMapper(temp_repo_with_external_apis)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalIntegrationService",
            file_path="src/main/java/com/example/ExternalIntegrationService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find AWS S3
        assert any("S3" in iface for iface in interfaces)

    def test_detect_http_url_calls(self, temp_repo_with_external_apis):
        """Test detecting direct URL/HTTP calls."""
        mapper = ScreenMapper(temp_repo_with_external_apis)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalIntegrationService",
            file_path="src/main/java/com/example/ExternalIntegrationService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find HTTP URL calls
        assert any("HttpURL" in iface or "external-api" in iface.lower() for iface in interfaces)

    def test_empty_controllers_returns_empty_interfaces(self):
        """Test that empty controller list returns no interfaces."""
        mapper = ScreenMapper(Path("/"))

        interfaces = mapper._find_external_interfaces([])
        assert len(interfaces) == 0


class TestFullScreenMappingWithBatchAndInterfaces:
    """Test complete mapping including batch jobs and external interfaces."""

    def test_full_mapping_includes_batch_and_interfaces(self, temp_repo_with_batch_jobs):
        """Test that full screen mapping includes batch jobs and interfaces."""
        from ai_discovery.menu_detector import Screen

        # Create a service that uses CUSTOMER table
        (temp_repo_with_batch_jobs / "src" / "main" / "java" / "com" / "example" / "CustomerService.java").write_text(
            """package com.example.service;

import com.example.entity.CustomerEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

@Service
public class CustomerService {
    private RestTemplate restTemplate;

    public void syncWithExternalSystem() {
        restTemplate.post("https://external-erp.com/sync", data);
    }
}
"""
        )

        # Create a controller
        (
            temp_repo_with_batch_jobs / "src" / "main" / "java" / "com" / "example" / "CustomerController.java"
        ).write_text(
            """package com.example.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/customers")
public class CustomerController {
    @Autowired
    private CustomerService customerService;

    @GetMapping("/search")
    public void search() {
        customerService.search();
    }
}
"""
        )

        mapper = ScreenMapper(temp_repo_with_batch_jobs)

        screen = Screen(
            screen_id="customer-search",
            menu_path=["Customers", "Search"],
            label="Search",
            path="/customers/search",
        )

        mapping = mapper.map_screen(screen)

        # Verify batch jobs detected
        assert len(mapping.batch_jobs) > 0

        # Verify external interfaces detected
        assert len(mapping.external_interfaces) > 0
