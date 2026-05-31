"""Real-world project validation tests for screen-centric pipeline."""

import json
import tempfile
from pathlib import Path
from typing import Tuple

import pytest
import yaml


def create_spring_boot_project() -> Path:
    """Create a realistic Spring Boot microservice repository."""
    tmpdir = tempfile.mkdtemp(prefix="spring-boot-")
    repo = Path(tmpdir)

    # pom.xml
    (repo / "pom.xml").write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>ecommerce-api</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.kafka</groupId>
            <artifactId>spring-kafka</artifactId>
        </dependency>
    </dependencies>
</project>
""")

    # Menu definition
    menu = {
        "items": [
            {
                "id": "catalog",
                "label": "Catalog Management",
                "path": "/catalog",
                "roles": ["ROLE_ADMIN"],
                "children": [
                    {"id": "products-list", "label": "Products", "path": "/catalog/products"},
                    {"id": "products-add", "label": "Add Product", "path": "/catalog/products/new"},
                    {"id": "products-edit", "label": "Edit Product", "path": "/catalog/products/:id/edit"},
                    {"id": "categories", "label": "Categories", "path": "/catalog/categories"},
                ]
            },
            {
                "id": "orders",
                "label": "Order Management",
                "path": "/orders",
                "roles": ["ROLE_ADMIN", "ROLE_USER"],
                "children": [
                    {"id": "orders-list", "label": "Orders", "path": "/orders"},
                    {"id": "orders-detail", "label": "Order Details", "path": "/orders/:id"},
                    {"id": "orders-invoice", "label": "Invoice", "path": "/orders/:id/invoice"},
                ]
            },
            {
                "id": "customers",
                "label": "Customers",
                "path": "/customers",
                "roles": ["ROLE_ADMIN"],
                "children": [
                    {"id": "customers-list", "label": "Customers", "path": "/customers"},
                    {"id": "customers-detail", "label": "Customer Detail", "path": "/customers/:id"},
                ]
            },
            {
                "id": "reports",
                "label": "Reports",
                "path": "/reports",
                "roles": ["ROLE_ADMIN"],
                "children": [
                    {"id": "reports-sales", "label": "Sales Report", "path": "/reports/sales"},
                    {"id": "reports-inventory", "label": "Inventory", "path": "/reports/inventory"},
                ]
            }
        ]
    }
    # Place menu in src/config where detector searches for it
    (repo / "src" / "config").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "config" / "menu.json").write_text(json.dumps(menu, indent=2))

    # Product entity
    (repo / "src" / "main" / "java" / "com" / "example" / "entity").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "main" / "java" / "com" / "example" / "entity" / "Product.java").write_text("""
package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "products")
public class Product {
    @Id
    @GeneratedValue
    private Long id;
    private String name;
    private String description;
    private BigDecimal price;
    private Integer stock;
}
""")

    # Order entity
    (repo / "src" / "main" / "java" / "com" / "example" / "entity" / "Order.java").write_text("""
package com.example.entity;

import javax.persistence.*;
import java.time.LocalDateTime;
import java.util.List;

@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue
    private Long id;

    @ManyToOne
    private Customer customer;

    @OneToMany
    private List<OrderItem> items;

    private LocalDateTime createdAt;
    private String status;
}
""")

    # Customer entity
    (repo / "src" / "main" / "java" / "com" / "example" / "entity" / "Customer.java").write_text("""
package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "customers")
public class Customer {
    @Id
    @GeneratedValue
    private Long id;
    private String name;
    private String email;
    private String address;
}
""")

    # Product repository
    (repo / "src" / "main" / "java" / "com" / "example" / "repository").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "main" / "java" / "com" / "example" / "repository" / "ProductRepository.java").write_text("""
package com.example.repository;

import com.example.entity.Product;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProductRepository extends JpaRepository<Product, Long> {
}
""")

    # Product service
    (repo / "src" / "main" / "java" / "com" / "example" / "service").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "main" / "java" / "com" / "example" / "service" / "ProductService.java").write_text("""
package com.example.service;

import com.example.entity.Product;
import com.example.repository.ProductRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import java.util.List;

@Service
public class ProductService {
    @Autowired
    private ProductRepository productRepository;

    public List<Product> getAllProducts() {
        return productRepository.findAll();
    }

    public Product getProduct(Long id) {
        return productRepository.findById(id).orElse(null);
    }
}
""")

    # Order service
    (repo / "src" / "main" / "java" / "com" / "example" / "service" / "OrderService.java").write_text("""
package com.example.service;

import com.example.entity.Order;
import com.example.repository.OrderRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import java.util.List;

@Service
public class OrderService {
    @Autowired
    private OrderRepository orderRepository;

    public List<Order> getOrders() {
        return orderRepository.findAll();
    }
}
""")

    # Product controller
    (repo / "src" / "main" / "java" / "com" / "example" / "controller").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "main" / "java" / "com" / "example" / "controller" / "ProductController.java").write_text("""
package com.example.controller;

import com.example.entity.Product;
import com.example.service.ProductService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/catalog/products")
public class ProductController {
    @Autowired
    private ProductService productService;

    @GetMapping
    public List<Product> list() {
        return productService.getAllProducts();
    }

    @GetMapping("/{id}")
    public Product get(@PathVariable Long id) {
        return productService.getProduct(id);
    }

    @PostMapping
    public Product create(@RequestBody Product product) {
        return null;
    }
}
""")

    # Order controller
    (repo / "src" / "main" / "java" / "com" / "example" / "controller" / "OrderController.java").write_text("""
package com.example.controller;

import com.example.entity.Order;
import com.example.service.OrderService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    @Autowired
    private OrderService orderService;

    @GetMapping
    public List<Order> list() {
        return orderService.getOrders();
    }

    @GetMapping("/{id}")
    public Order get(@PathVariable Long id) {
        return null;
    }
}
""")

    # Batch job
    (repo / "src" / "main" / "java" / "com" / "example" / "batch").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "main" / "java" / "com" / "example" / "batch" / "OrderExportJob.java").write_text("""
package com.example.batch;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class OrderExportJob {
    @Scheduled(cron = "0 0 2 * * *")  // 2 AM daily
    public void exportOrders() {
        // Export orders to CSV/FTP
    }
}
""")

    return repo


def create_aspnet_core_project() -> Path:
    """Create a realistic ASP.NET Core API repository."""
    tmpdir = tempfile.mkdtemp(prefix="aspnet-core-")
    repo = Path(tmpdir)

    # appsettings.json
    (repo / "appsettings.json").write_text("""{
  "Logging": {
    "LogLevel": {
      "Default": "Information"
    }
  },
  "AllowedHosts": "*"
}
""")

    # .csproj
    (repo / "EcommerceApi.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk.Web">
    <PropertyGroup>
        <TargetFramework>net6.0</TargetFramework>
        <ImplicitUsings>enable</ImplicitUsings>
        <Nullable>enable</Nullable>
    </PropertyGroup>
    <ItemGroup>
        <PackageReference Include="Microsoft.EntityFrameworkCore" Version="6.0.0" />
        <PackageReference Include="Microsoft.AspNetCore.App" Version="6.0.0" />
    </ItemGroup>
</Project>
""")

    # Menu definition
    menu = {
        "items": [
            {
                "id": "products",
                "label": "Products",
                "path": "/products",
                "children": [
                    {"id": "products-list", "label": "All Products", "path": "/products"},
                    {"id": "products-create", "label": "Add New", "path": "/products/create"},
                ]
            },
            {
                "id": "orders",
                "label": "Orders",
                "path": "/orders",
                "children": [
                    {"id": "orders-list", "label": "Orders", "path": "/orders"},
                    {"id": "orders-detail", "label": "Details", "path": "/orders/:id"},
                ]
            }
        ]
    }
    (repo / "config" / "menu.json").parent.mkdir(parents=True, exist_ok=True)
    (repo / "config" / "menu.json").write_text(json.dumps(menu, indent=2))

    # Models
    (repo / "Models").mkdir(parents=True, exist_ok=True)
    (repo / "Models" / "Product.cs").write_text("""
using System.ComponentModel.DataAnnotations;

namespace EcommerceApi.Models
{
    [Table("products")]
    public class Product
    {
        [Key]
        public int Id { get; set; }
        public string Name { get; set; }
        public string Description { get; set; }
        public decimal Price { get; set; }
    }
}
""")

    (repo / "Models" / "Order.cs").write_text("""
using System.ComponentModel.DataAnnotations;

namespace EcommerceApi.Models
{
    [Table("orders")]
    public class Order
    {
        [Key]
        public int Id { get; set; }
        public int CustomerId { get; set; }
        public DateTime CreatedAt { get; set; }
        public string Status { get; set; }
    }
}
""")

    # Controllers
    (repo / "Controllers").mkdir(parents=True, exist_ok=True)
    (repo / "Controllers" / "ProductsController.cs").write_text("""
using Microsoft.AspNetCore.Mvc;
using EcommerceApi.Models;

namespace EcommerceApi.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class ProductsController : ControllerBase
    {
        [HttpGet]
        public async Task<ActionResult<IEnumerable<Product>>> GetProducts()
        {
            return Ok();
        }

        [HttpGet("{id}")]
        public async Task<ActionResult<Product>> GetProduct(int id)
        {
            return Ok();
        }

        [HttpPost]
        public async Task<ActionResult<Product>> CreateProduct(Product product)
        {
            return CreatedAtAction(nameof(GetProduct), new { id = product.Id }, product);
        }
    }
}
""")

    (repo / "Controllers" / "OrdersController.cs").write_text("""
using Microsoft.AspNetCore.Mvc;
using EcommerceApi.Models;

namespace EcommerceApi.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class OrdersController : ControllerBase
    {
        [HttpGet]
        public async Task<ActionResult<IEnumerable<Order>>> GetOrders()
        {
            return Ok();
        }

        [HttpGet("{id}")]
        public async Task<ActionResult<Order>> GetOrder(int id)
        {
            return Ok();
        }
    }
}
""")

    # Services
    (repo / "Services").mkdir(parents=True, exist_ok=True)
    (repo / "Services" / "ProductService.cs").write_text("""
using EcommerceApi.Models;

namespace EcommerceApi.Services
{
    public class ProductService
    {
        private readonly IRepository<Product> _repository;

        public ProductService(IRepository<Product> repository)
        {
            _repository = repository;
        }

        public async Task<List<Product>> GetAllProducts()
        {
            return await _repository.GetAllAsync();
        }
    }
}
""")

    return repo


def create_express_project() -> Path:
    """Create a realistic Express.js REST API repository."""
    tmpdir = tempfile.mkdtemp(prefix="express-api-")
    repo = Path(tmpdir)

    # package.json
    (repo / "package.json").write_text("""{
  "name": "ecommerce-api",
  "version": "1.0.0",
  "description": "E-commerce REST API",
  "main": "src/index.js",
  "dependencies": {
    "express": "^4.18.0",
    "mongoose": "^6.0.0",
    "dotenv": "^16.0.0"
  }
}
""")

    # Menu definition
    menu = {
        "items": [
            {
                "id": "products",
                "label": "Products",
                "path": "/products",
                "children": [
                    {"id": "products-list", "label": "List", "path": "/products"},
                    {"id": "products-search", "label": "Search", "path": "/products/search"},
                    {"id": "products-detail", "label": "Detail", "path": "/products/:id"},
                ]
            },
            {
                "id": "orders",
                "label": "Orders",
                "path": "/orders",
                "children": [
                    {"id": "orders-list", "label": "Orders", "path": "/orders"},
                    {"id": "orders-create", "label": "Create", "path": "/orders"},
                ]
            }
        ]
    }
    (repo / "config").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "menu.json").write_text(json.dumps(menu, indent=2))

    # Models
    (repo / "src" / "models").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "models" / "Product.js").write_text("""
const mongoose = require('mongoose');

const productSchema = new mongoose.Schema({
  name: String,
  description: String,
  price: Number,
  stock: Number,
  createdAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('Product', productSchema);
""")

    (repo / "src" / "models" / "Order.js").write_text("""
const mongoose = require('mongoose');

const orderSchema = new mongoose.Schema({
  customerId: mongoose.Schema.Types.ObjectId,
  items: [Object],
  status: String,
  createdAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('Order', orderSchema);
""")

    # Services
    (repo / "src" / "services").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "services" / "ProductService.js").write_text("""
const Product = require('../models/Product');

class ProductService {
  async getAllProducts() {
    return Product.find();
  }

  async getProduct(id) {
    return Product.findById(id);
  }

  async createProduct(data) {
    const product = new Product(data);
    return product.save();
  }
}

module.exports = new ProductService();
""")

    (repo / "src" / "services" / "OrderService.js").write_text("""
const Order = require('../models/Order');

class OrderService {
  async getAllOrders() {
    return Order.find();
  }

  async getOrder(id) {
    return Order.findById(id);
  }
}

module.exports = new OrderService();
""")

    # Routes
    (repo / "src" / "routes").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "routes" / "products.js").write_text("""
const express = require('express');
const ProductService = require('../services/ProductService');

const router = express.Router();

router.get('/', async (req, res) => {
  const products = await ProductService.getAllProducts();
  res.json(products);
});

router.get('/search', async (req, res) => {
  // Search implementation
  res.json([]);
});

router.get('/:id', async (req, res) => {
  const product = await ProductService.getProduct(req.params.id);
  res.json(product);
});

module.exports = router;
""")

    (repo / "src" / "routes" / "orders.js").write_text("""
const express = require('express');
const OrderService = require('../services/OrderService');

const router = express.Router();

router.get('/', async (req, res) => {
  const orders = await OrderService.getAllOrders();
  res.json(orders);
});

router.post('/', async (req, res) => {
  // Create order
  res.json({});
});

module.exports = router;
""")

    return repo


@pytest.fixture
def spring_project():
    """Fixture providing a Spring Boot test project."""
    repo = create_spring_boot_project()
    yield repo
    # Cleanup happens via tempdir


@pytest.fixture
def aspnet_project():
    """Fixture providing an ASP.NET Core test project."""
    repo = create_aspnet_core_project()
    yield repo


@pytest.fixture
def express_project():
    """Fixture providing an Express.js test project."""
    repo = create_express_project()
    yield repo


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
