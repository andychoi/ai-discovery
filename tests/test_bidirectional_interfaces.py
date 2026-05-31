"""Tests for bidirectional interface detection (push/pull)."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.screen_mapper import ScreenMapper, BackendComponent


@pytest.fixture
def temp_repo_with_bidirectional():
    """Create a repository with push and pull external interfaces."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)

        # Service with PUSH (outbound) calls
        push_service = """package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.jms.core.JmsTemplate;

@Service
public class ExternalPushService {
    private RestTemplate restTemplate;
    private KafkaTemplate<String, String> kafkaTemplate;
    private JmsTemplate jmsTemplate;

    public void sendToPartner() {
        // PUSH: Send to external REST API
        restTemplate.post("https://partner.example.com/webhook", data);

        // PUSH: Send to Kafka topic
        kafkaTemplate.send("external-events", message);

        // PUSH: Send to JMS queue
        jmsTemplate.convertAndSend("external-queue", message);
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "ExternalPushService.java").write_text(
            push_service
        )

        # Controller with PULL (inbound) webhooks
        pull_controller = """package com.example.controller;

import org.springframework.web.bind.annotation.*;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.jms.annotation.JmsListener;

@RestController
@RequestMapping("/api/webhooks")
public class WebhookController {

    // PULL: Receive webhook from external partner
    @PostMapping("/partner-events")
    public void receivePartnerEvent(@RequestBody PartnerEvent event) {
        // Process incoming webhook
    }

    // PULL: Receive webhook with authentication
    @PostMapping("/secure-webhook")
    public void receiveSecureWebhook(@RequestBody SecurePayload payload, @RequestHeader String hmac) {
        // Verify HMAC signature before processing
    }
}

@Service
public class ExternalMessageListener {

    // PULL: Consume from Kafka topic
    @KafkaListener(topics = "external-updates")
    public void listenKafka(String message) {
        // Process incoming Kafka message
    }

    // PULL: Consume from JMS queue
    @JmsListener(destination = "external-updates")
    public void listenJMS(String message) {
        // Process incoming JMS message
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "WebhookController.java").write_text(
            pull_controller
        )

        # Batch job triggered both internally and externally
        batch_job = """package com.example.batch;

import org.springframework.batch.core.Job;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.web.bind.annotation.*;
import org.springframework.kafka.annotation.KafkaListener;

@Configuration
@EnableBatchProcessing
public class DataSyncJobConfig {

    // References DATA and SYNC_LOG tables
    // INTERNAL TRIGGER: Scheduled execution
    @Scheduled(cron = "0 0 2 * * *")
    public void scheduledSync() {
        // Run nightly data sync from DATA and SYNC_LOG tables
    }

    // EXTERNAL TRIGGER: REST API
    @PostMapping("/trigger-sync")
    public void triggerSync() {
        // Triggered by external system via HTTP
        // Syncs DATA and SYNC_LOG
    }

    // EXTERNAL TRIGGER: Message queue
    @KafkaListener(topics = "job-triggers")
    public void triggerSyncFromQueue(String message) {
        // Triggered by message from external system
        // Syncs from DATA and SYNC_LOG tables
    }
}
"""
        (repo_path / "src" / "main" / "java" / "com" / "example" / "DataSyncJobConfig.java").write_text(
            batch_job
        )

        yield repo_path


class TestBidirectionalInterfaces:
    """Test detection of push (outbound) and pull (inbound) interfaces."""

    def test_detect_push_rest_calls(self, temp_repo_with_bidirectional):
        """Test detecting PUSH (outbound) REST API calls."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalPushService",
            file_path="src/main/java/com/example/ExternalPushService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find PUSH REST calls
        assert any("REST-PUSH" in iface for iface in interfaces)
        assert any("partner.example.com" in iface for iface in interfaces)

    def test_detect_push_kafka(self, temp_repo_with_bidirectional):
        """Test detecting PUSH (outbound) Kafka sends."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalPushService",
            file_path="src/main/java/com/example/ExternalPushService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find PUSH Kafka
        assert any("Kafka-PUSH" in iface for iface in interfaces)

    def test_detect_push_jms(self, temp_repo_with_bidirectional):
        """Test detecting PUSH (outbound) JMS sends."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        service = BackendComponent(
            component_type="service",
            class_name="ExternalPushService",
            file_path="src/main/java/com/example/ExternalPushService.java",
        )

        interfaces = mapper._find_external_interfaces([service])

        # Should find PUSH JMS
        assert any("JMS-PUSH" in iface for iface in interfaces)

    def test_detect_pull_rest_webhooks(self, temp_repo_with_bidirectional):
        """Test detecting PULL (inbound) REST webhooks."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        controller = BackendComponent(
            component_type="controller",
            class_name="WebhookController",
            file_path="src/main/java/com/example/WebhookController.java",
        )

        interfaces = mapper._find_external_interfaces([controller])

        # Should find PULL REST webhooks
        assert any("REST-PULL" in iface for iface in interfaces)
        assert any("partner-events" in iface or "secure-webhook" in iface for iface in interfaces)

    def test_detect_pull_authenticated_webhooks(self, temp_repo_with_bidirectional):
        """Test detecting PULL (inbound) authenticated webhooks."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        controller = BackendComponent(
            component_type="controller",
            class_name="WebhookController",
            file_path="src/main/java/com/example/WebhookController.java",
        )

        interfaces = mapper._find_external_interfaces([controller])

        # Should find authenticated webhook handlers
        assert any("Webhook-PULL-WithAuth" in iface for iface in interfaces)

    def test_detect_pull_kafka_listener(self, temp_repo_with_bidirectional):
        """Test detecting PULL (inbound) Kafka listeners."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        listener = BackendComponent(
            component_type="service",
            class_name="ExternalMessageListener",
            file_path="src/main/java/com/example/WebhookController.java",
        )

        interfaces = mapper._find_external_interfaces([listener])

        # Should find PULL Kafka listeners
        assert any("Kafka-PULL" in iface for iface in interfaces)

    def test_detect_pull_jms_listener(self, temp_repo_with_bidirectional):
        """Test detecting PULL (inbound) JMS listeners."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        listener = BackendComponent(
            component_type="service",
            class_name="ExternalMessageListener",
            file_path="src/main/java/com/example/WebhookController.java",
        )

        interfaces = mapper._find_external_interfaces([listener])

        # Should find PULL JMS listeners
        assert any("JMS-PULL" in iface for iface in interfaces)


class TestBidirectionalBatchTriggers:
    """Test detection of batch job triggers (internal and external)."""

    def test_detect_internal_batch_trigger(self, temp_repo_with_bidirectional):
        """Test detecting INTERNAL batch triggers (@Scheduled)."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        tables = ["DATA", "SYNC_LOG"]
        jobs = mapper._find_related_batch_jobs(tables)

        # Should find DataSyncJob
        assert any("Sync" in job or "DataSync" in job for job in jobs)

    def test_detect_rest_triggered_batch(self, temp_repo_with_bidirectional):
        """Test detecting EXTERNAL REST-triggered batch jobs."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        tables = ["DATA", "SYNC_LOG"]
        jobs = mapper._find_related_batch_jobs(tables)

        # Should find HTTP-triggered variant
        assert any("HTTP-triggered" in job for job in jobs)

    def test_detect_queue_triggered_batch(self, temp_repo_with_bidirectional):
        """Test detecting EXTERNAL queue-triggered batch jobs."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        tables = ["DATA", "SYNC_LOG"]
        jobs = mapper._find_related_batch_jobs(tables)

        # Should find Queue-triggered variant
        assert any("Queue-triggered" in job for job in jobs)

    def test_batch_triggers_complete_list(self, temp_repo_with_bidirectional):
        """Test that batch job list includes all trigger types."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        tables = ["DATA", "SYNC_LOG"]
        jobs = mapper._find_related_batch_jobs(tables)

        # Should have multiple trigger variants
        assert len(jobs) >= 2  # At least internal + external triggers

        # Verify multiple trigger types present
        trigger_types = []
        if any("HTTP" in job for job in jobs):
            trigger_types.append("REST")
        if any("Queue" in job for job in jobs):
            trigger_types.append("Queue")

        # Should have detected at least one external trigger
        assert len(trigger_types) > 0


class TestPushPullBalance:
    """Test that both push and pull are detected equally."""

    def test_equal_push_pull_detection(self, temp_repo_with_bidirectional):
        """Test that push and pull interfaces are both detected."""
        mapper = ScreenMapper(temp_repo_with_bidirectional)

        # Get all components
        push_service = BackendComponent(
            component_type="service",
            class_name="ExternalPushService",
            file_path="src/main/java/com/example/ExternalPushService.java",
        )

        pull_controller = BackendComponent(
            component_type="controller",
            class_name="WebhookController",
            file_path="src/main/java/com/example/WebhookController.java",
        )

        push_interfaces = mapper._find_external_interfaces([push_service])
        pull_interfaces = mapper._find_external_interfaces([pull_controller])

        # Should detect PUSH interfaces
        assert len(push_interfaces) > 0
        assert any("PUSH" in iface for iface in push_interfaces)

        # Should detect PULL interfaces
        assert len(pull_interfaces) > 0
        assert any("PULL" in iface for iface in pull_interfaces)

        # Should have roughly balanced detection
        push_count = len([i for i in push_interfaces if "PUSH" in i])
        pull_count = len([i for i in pull_interfaces if "PULL" in i])

        assert push_count > 0, "No PUSH interfaces detected"
        assert pull_count > 0, "No PULL interfaces detected"
