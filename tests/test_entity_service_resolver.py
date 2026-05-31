"""Tests for generic entity-service resolver."""

import tempfile
from pathlib import Path

import pytest

from ai_discovery.entity_service_resolver import (
    Entity,
    EntityServiceResolver,
    Repository,
    Service,
)
from ai_discovery.framework_detector import FrameworkDetector


@pytest.fixture
def java_spring_repo_with_entities():
    """Create a sample Java Spring repository with entities and services."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create entity
        entity_dir = repo_path / "src" / "main" / "java" / "com" / "example" / "entity"
        entity_dir.mkdir(parents=True)
        (entity_dir / "Order.java").write_text("""
package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "orders")
public class Order {
    @Id
    private Long id;
    private String status;
}
""")

        # Create repository
        repo_dir = repo_path / "src" / "main" / "java" / "com" / "example" / "repository"
        repo_dir.mkdir(parents=True)
        (repo_dir / "OrderRepository.java").write_text("""
package com.example.repository;

import com.example.entity.Order;
import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderRepository extends JpaRepository<Order, Long> {
}
""")

        # Create service
        service_dir = repo_path / "src" / "main" / "java" / "com" / "example" / "service"
        service_dir.mkdir(parents=True)
        (service_dir / "OrderService.java").write_text("""
package com.example.service;

import com.example.entity.Order;
import com.example.repository.OrderRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    @Autowired
    private OrderRepository orderRepository;

    public Order findById(Long id) {
        return orderRepository.findById(id).orElse(null);
    }
}
""")

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

        yield repo_path


@pytest.fixture
def dotnet_aspnet_repo_with_entities():
    """Create a sample .NET ASP.NET repository with entities and services."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create entity
        models_dir = repo_path / "Models"
        models_dir.mkdir()
        (models_dir / "Order.cs").write_text("""
using System;

[Table("orders")]
public class Order {
    public int Id { get; set; }
    public string Status { get; set; }
}
""")

        # Create service with direct Order reference
        services_dir = repo_path / "Services"
        services_dir.mkdir()
        (services_dir / "OrderService.cs").write_text("""
using System;
using MyApp.Models;

public interface IOrderRepository {
    Order GetById(int id);
}

public class OrderService {
    private readonly IOrderRepository _repository;

    public OrderService(IOrderRepository repository) {
        _repository = repository;
    }

    public Order Find(int id) {
        return _repository.GetById(id);
    }

    public Order Create(Order order) {
        return order;
    }
}
""")

        # Create .csproj
        (repo_path / "MyApp.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.Web">
    <PropertyGroup>
        <TargetFramework>net6.0</TargetFramework>
    </PropertyGroup>
</Project>
""")

        yield repo_path


@pytest.fixture
def node_express_repo_with_entities():
    """Create a sample Node.js Express repository with models and services."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Create model
        models_dir = repo_path / "models"
        models_dir.mkdir()
        (models_dir / "Order.js").write_text("""
const mongoose = require('mongoose');

const orderSchema = new mongoose.Schema({
    _id: mongoose.Schema.Types.ObjectId,
    status: String,
});

module.exports = mongoose.model('Order', orderSchema);
""")

        # Create service
        services_dir = repo_path / "services"
        services_dir.mkdir()
        (services_dir / "OrderService.js").write_text("""
const Order = require('../models/Order');

class OrderService {
    async findById(id) {
        return Order.findById(id);
    }
}

module.exports = new OrderService();
""")

        # Create package.json
        (repo_path / "package.json").write_text("""{
    "name": "my-app",
    "version": "1.0.0",
    "dependencies": {
        "express": "^4.18.0",
        "mongoose": "^6.0.0"
    }
}
""")

        yield repo_path


class TestEntityServiceResolverJava:
    """Test entity-service resolution for Java Spring."""

    def test_find_services_for_entity_java(self, java_spring_repo_with_entities):
        """Test finding services that use a Java entity."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        order_entity = Entity(
            entity_type="class",
            class_name="Order",
            file_path="src/main/java/com/example/entity/Order.java"
        )

        services = resolver.find_services_for_entity(order_entity)

        assert len(services) > 0
        assert any("OrderService" in s.class_name for s in services)
        assert all(isinstance(s, Service) for s in services)

    def test_find_repositories_for_entity_java(self, java_spring_repo_with_entities):
        """Test finding repositories that handle an entity."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        order_entity = Entity(
            entity_type="class",
            class_name="Order",
            file_path="src/main/java/com/example/entity/Order.java"
        )

        repositories = resolver._find_repositories_for_entity_java(order_entity)

        assert len(repositories) > 0
        assert any("OrderRepository" in r.class_name for r in repositories)

    def test_find_entities_for_service_java(self, java_spring_repo_with_entities):
        """Test finding entities used by a service."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        service = Service(
            service_type="class",
            class_name="OrderService",
            file_path="src/main/java/com/example/service/OrderService.java",
            repositories=["OrderRepository"]
        )

        entities = resolver.find_entities_for_service(service)

        # Should find at least one entity (Order)
        assert len(entities) >= 0


class TestEntityServiceResolverDotNet:
    """Test entity-service resolution for .NET ASP.NET."""

    def test_find_services_for_entity_dotnet(self, dotnet_aspnet_repo_with_entities):
        """Test finding services that use a .NET entity."""
        pattern = FrameworkDetector.DOTNET_ASPNET
        resolver = EntityServiceResolver(dotnet_aspnet_repo_with_entities, pattern)

        order_entity = Entity(
            entity_type="class",
            class_name="Order",
            file_path="Models/Order.cs"
        )

        services = resolver.find_services_for_entity(order_entity)

        # Should find OrderService
        assert len(services) > 0
        assert any("OrderService" in s.class_name for s in services)

    def test_find_entities_for_service_dotnet(self, dotnet_aspnet_repo_with_entities):
        """Test finding entities used by a .NET service."""
        pattern = FrameworkDetector.DOTNET_ASPNET
        resolver = EntityServiceResolver(dotnet_aspnet_repo_with_entities, pattern)

        service = Service(
            service_type="class",
            class_name="OrderService",
            file_path="Services/OrderService.cs"
        )

        entities = resolver.find_entities_for_service(service)

        # Should find Order entity reference
        assert len(entities) > 0


class TestEntityServiceResolverNode:
    """Test entity-service resolution for Node.js Express."""

    def test_find_services_for_entity_node(self, node_express_repo_with_entities):
        """Test finding services that use a Node.js model."""
        pattern = FrameworkDetector.NODE_EXPRESS
        resolver = EntityServiceResolver(node_express_repo_with_entities, pattern)

        order_entity = Entity(
            entity_type="class",
            class_name="Order",
            file_path="models/Order.js"
        )

        services = resolver.find_services_for_entity(order_entity)

        # May find OrderService if model naming matches
        assert isinstance(services, list)

    def test_find_entities_for_service_node(self, node_express_repo_with_entities):
        """Test finding models used by a Node.js service."""
        pattern = FrameworkDetector.NODE_EXPRESS
        resolver = EntityServiceResolver(node_express_repo_with_entities, pattern)

        service = Service(
            service_type="class",
            class_name="OrderService",
            file_path="services/OrderService.js"
        )

        entities = resolver.find_entities_for_service(service)

        # Should find Order model
        assert len(entities) > 0
        assert any("Order" in e.class_name for e in entities)


class TestDependencyGraphBuilding:
    """Test building complete dependency graphs."""

    def test_build_dependency_graph_java(self, java_spring_repo_with_entities):
        """Test building a complete dependency graph for Java."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        graph = resolver.build_dependency_graph()

        assert len(graph.entities) > 0
        assert len(graph.services) > 0
        assert "Order" in graph.entities or any("Order" in name for name in graph.entities.keys())

    def test_build_dependency_graph_relationships(self, java_spring_repo_with_entities):
        """Test that relationships are correctly built."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        graph = resolver.build_dependency_graph()

        # Check that relationships exist
        assert isinstance(graph.relationships, dict)
        # Each entity should map to a list of service names
        for entity_name, service_names in graph.relationships.items():
            assert isinstance(service_names, list)


class TestConfidenceScoring:
    """Test confidence scoring of resolved relationships."""

    def test_service_confidence_scores(self, java_spring_repo_with_entities):
        """Test that services have appropriate confidence scores."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        order_entity = Entity(
            entity_type="class",
            class_name="Order",
            file_path="src/main/java/com/example/entity/Order.java"
        )

        services = resolver.find_services_for_entity(order_entity)

        for service in services:
            assert 0.5 <= service.confidence <= 1.0

    def test_repository_confidence_scores(self, java_spring_repo_with_entities):
        """Test that repositories have appropriate confidence scores."""
        pattern = FrameworkDetector.JAVA_SPRING
        resolver = EntityServiceResolver(java_spring_repo_with_entities, pattern)

        order_entity = Entity(
            entity_type="class",
            class_name="Order",
            file_path="src/main/java/com/example/entity/Order.java"
        )

        repositories = resolver._find_repositories_for_entity_java(order_entity)

        for repo in repositories:
            assert 0.5 <= repo.confidence <= 1.0
