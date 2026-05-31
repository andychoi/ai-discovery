# Entity-Service Resolver Guide

## Overview

The entity-service resolver is a generic framework for tracing which service classes handle which data entities, regardless of language or framework.

**Problem it solves**: Given `OrderEntity`, which service methods read/write it? In Spring, you'd grep for `OrderEntity` in service files. In ASP.NET, you'd look for `DbSet<Order>` in repositories. In Node, you'd search Mongoose models for references.

**Solution**: A pluggable resolver that learns framework-specific patterns and automatically builds the relationship graph.

## Architecture

### Core Concept: Dependency Graph

```
Entity (OrderEntity)
  ↓ (read by)
Repository (OrderRepository)
  ↓ (injected into)
Service (OrderService)
  ↓ (called by)
Controller (OrderController)
  ↓ (invoked by)
Frontend API Call
```

The resolver works backward and forward:
- **Backward**: Entity → Repository → Service → Controller
- **Forward**: Frontend call → Controller → Service → Entity

### Generic Patterns

Every framework has the same patterns:

| Step | Java/Spring | .NET/ASP.NET | Node.js |
|------|------------|------------|---------|
| **Entity** | `@Entity class Order` | `[Table] public class Order` | `const orderSchema = new Schema({...})` |
| **Repository** | `interface OrderRepo extends JpaRepository<Order>` | `interface IOrderRepo : IRepository<Order>` | `const Order = mongoose.model('Order', schema)` |
| **Service** | `@Service class OrderSvc { @Autowired OrderRepo repo; }` | `[Service] class OrderSvc { public OrderSvc(IOrderRepo repo) {...} }` | `class OrderService { constructor(private orderModel) {...} }` |
| **Controller** | `@RestController @Autowired OrderSvc svc;` | `[ApiController] public OrderController(OrderSvc svc) {...}` | `router.get('/orders', orderService.list)` |

### Implementation Strategy

1. **Parse framework-specific annotations/attributes**
   - Java: `@Entity`, `@Repository`, `@Autowired`, `@Inject`
   - .NET: `[Table]`, `[Entity]`, `[Service]`, `[Inject]`
   - Node: `mongoose.model()`, `require()`, constructor injection

2. **Extract dependency declarations**
   - Java: Look for `private OrderRepo repo;` with `@Autowired`
   - .NET: Constructor parameters `public Service(IOrderRepo repo) {...}`
   - Node: Constructor assignment `this.orderModel = orderModel`

3. **Build relationship graph**
   - Entity → Repository (via type reference)
   - Repository → Service (via constructor injection)
   - Service → Controller (via constructor injection)

4. **Resolve entity operations**
   - Which methods read? (`findById`, `findAll`, `SELECT *`)
   - Which methods write? (`save`, `delete`, `INSERT`, `UPDATE`)
   - Which methods trigger batch jobs? (`@Scheduled`, `@PostMapping`)

## Usage in ai-discovery

### Built-In Entity-Service Resolution

The `screen_mapper.py` already performs basic resolution:

```python
# Find services used by a controller
controller = BackendComponent(
    component_type="controller",
    class_name="OrderController",
    file_path="src/.../OrderController.java"
)

services = mapper._resolve_to_services(controller)
# Returns: [OrderService, CustomerService]

# Extract database tables from services
tables = mapper._extract_db_tables(services)
# Returns: ["ORDER", "CUSTOMER", "CUSTOMER_ADDRESS"]
```

### Advanced: Generic Resolver

For complex inheritance chains (interface-based repositories, abstract services), use the generic resolver:

```python
from ai_discovery.entity_service_resolver import EntityServiceResolver

resolver = EntityServiceResolver(repo_path, framework_pattern)

# Find which service uses OrderEntity
order_entity = Entity(
    entity_type="class",
    class_name="OrderEntity",
    file_path="src/.../OrderEntity.java"
)

services = resolver.find_services_for_entity(order_entity)
# Returns: [OrderService, OrderAuditService]

# Find which entities a service uses
order_service = Service(
    service_type="class",
    class_name="OrderService",
    file_path="src/.../OrderService.java"
)

entities = resolver.find_entities_for_service(order_service)
# Returns: [OrderEntity, CustomerEntity, OrderItemEntity]

# Get complete dependency graph
graph = resolver.build_dependency_graph()
# Returns: { entities: {...}, services: {...}, repositories: {...}, controllers: {...} }
```

## Implementing the Resolver

### Step 1: Define Data Models

```python
@dataclass
class Entity:
    entity_type: str  # "class", "table", "schema"
    class_name: str
    file_path: str
    fields: list[str] = field(default_factory=list)

@dataclass
class Repository:
    repo_type: str  # "interface", "class"
    class_name: str
    file_path: str
    entity_type: str  # What entity does it manage?

@dataclass
class Service:
    service_type: str  # "class", "interface"
    class_name: str
    file_path: str
    repositories: list[str] = field(default_factory=list)  # Injected repos

@dataclass
class DependencyGraph:
    entities: dict[str, Entity]
    repositories: dict[str, Repository]
    services: dict[str, Service]
    controllers: dict[str, Controller]
    relationships: dict[str, list[str]]  # entity -> services that use it
```

### Step 2: Implement Framework-Specific Extractors

```python
class EntityServiceResolver:
    def __init__(self, repo_path: Path, pattern: FrameworkPattern):
        self.repo_path = repo_path
        self.pattern = pattern
        
    def find_services_for_entity(self, entity: Entity) -> list[Service]:
        """Find services that depend on this entity."""
        services = []
        
        # Framework-specific search
        if self.pattern.framework == "spring":
            # Look for @Repository that uses this entity
            # Then find @Service that injects that repository
            repositories = self._find_repositories_for_entity_java(entity)
            for repo in repositories:
                services.extend(self._find_services_for_repository_java(repo))
        
        elif self.pattern.framework == "aspnet":
            # Look for IRepository<Entity> in service constructors
            services = self._find_services_for_entity_dotnet(entity)
        
        elif self.pattern.framework == "express":
            # Look for mongoose.model() and its usage
            services = self._find_services_for_entity_node(entity)
        
        return services
    
    def _find_repositories_for_entity_java(self, entity: Entity) -> list[Repository]:
        """Find Java @Repository interfaces that handle this entity."""
        repositories = []
        repo_files = list(self.repo_path.glob("**/*Repository.java"))
        
        for repo_file in repo_files:
            content = repo_file.read_text(errors="ignore")
            
            # Look for "extends JpaRepository<OrderEntity>" pattern
            if f"<{entity.class_name}" in content or f"<{entity.class_name}," in content:
                class_match = re.search(r'(?:interface|class)\s+(\w+)', content)
                if class_match:
                    repositories.append(Repository(
                        repo_type="interface",
                        class_name=class_match.group(1),
                        file_path=str(repo_file.relative_to(self.repo_path)),
                        entity_type=entity.class_name
                    ))
        
        return repositories
    
    def _find_services_for_repository_java(self, repository: Repository) -> list[Service]:
        """Find Java @Service classes that inject this repository."""
        services = []
        service_files = list(self.repo_path.glob("**/*Service.java"))
        
        for service_file in service_files:
            content = service_file.read_text(errors="ignore")
            
            # Look for "@Autowired private RepositoryName repo;" pattern
            pattern = rf'@(?:Autowired|Inject)\s+private\s+{repository.class_name}\s+\w+;'
            if re.search(pattern, content):
                class_match = re.search(r'public\s+class\s+(\w+)', content)
                if class_match:
                    services.append(Service(
                        service_type="class",
                        class_name=class_match.group(1),
                        file_path=str(service_file.relative_to(self.repo_path)),
                        repositories=[repository.class_name]
                    ))
        
        return services
```

### Step 3: Build Complete Graph

```python
def build_dependency_graph(self) -> DependencyGraph:
    """Build complete entity-repository-service dependency graph."""
    
    # Find all entities
    entities = self._find_all_entities()
    
    # Find all repositories
    repositories = self._find_all_repositories()
    
    # Find all services
    services = self._find_all_services()
    
    # Build relationships
    relationships = {}
    for entity in entities.values():
        services_for_entity = self.find_services_for_entity(entity)
        relationships[entity.class_name] = [s.class_name for s in services_for_entity]
    
    return DependencyGraph(
        entities=entities,
        repositories=repositories,
        services=services,
        controllers=self._find_all_controllers(),
        relationships=relationships
    )
```

## Use Cases

### Use Case 1: Find All Services That Use OrderEntity

```python
resolver = EntityServiceResolver(repo_path, pattern)

order_entity = Entity(
    entity_type="class",
    class_name="OrderEntity",
    file_path="src/.../OrderEntity.java"
)

services = resolver.find_services_for_entity(order_entity)
# Returns: [OrderService, OrderAuditService, OrderExportService]
```

### Use Case 2: Generate Service-Entity Matrix

```python
graph = resolver.build_dependency_graph()

# Create matrix: rows = services, columns = entities, X = uses entity
for entity_name, service_list in graph.relationships.items():
    for service_name in service_list:
        print(f"{service_name} uses {entity_name}")
```

### Use Case 3: Find Untested Entities

```python
graph = resolver.build_dependency_graph()

# Find entities with no services (orphaned)
test_coverage = {}
for entity in graph.entities.values():
    test_coverage[entity.class_name] = len(graph.relationships.get(entity.class_name, []))

untested = [e for e, count in test_coverage.items() if count == 0]
# Returns: [InternalAuditLog, AnalyticsEvent]  <- not used by any service
```

## Challenges & Limitations

### Challenge 1: Generic Type Parameters

Java: `JpaRepository<OrderEntity, Long>` — need to extract first type param
.NET: `IRepository<Order>` — same pattern
Node: `mongoose.model('Order', schema)` — second parameter is schema

**Solution**: Parse type parameters in order of appearance

### Challenge 2: Indirect Injection

```java
// Direct injection
@Service
public class OrderService {
    @Autowired
    private OrderRepository repo;  // Easy to find
}

// Indirect: factory pattern
@Service
public class OrderService {
    private OrderRepository repo;
    
    @Autowired
    public OrderService(RepositoryFactory factory) {
        this.repo = factory.create(OrderRepository.class);  // Hard to find
    }
}
```

**Solution**: Add factory pattern detection, but accept some false negatives

### Challenge 3: Multiple Frameworks

When a codebase uses multiple frameworks (e.g., Spring AND Quarkus), apply resolver for each framework separately, then merge results

### Challenge 4: Dynamic Proxies / CGLib

Spring proxies entities at runtime; static analysis sees the proxy, not the real class.

**Solution**: Look for proxy annotations (`@Transactional`, `@Cacheable`) and resolve underlying class

## Testing the Resolver

### Test 1: Basic Entity-Service Linking

```python
def test_resolve_spring_service_for_entity(spring_repo):
    resolver = EntityServiceResolver(spring_repo, FrameworkDetector.JAVA_SPRING)
    
    order_entity = Entity(
        entity_type="class",
        class_name="OrderEntity",
        file_path="src/.../OrderEntity.java"
    )
    
    services = resolver.find_services_for_entity(order_entity)
    
    assert len(services) > 0
    assert any("OrderService" in s.class_name for s in services)
```

### Test 2: Complete Dependency Graph

```python
def test_build_dependency_graph(spring_repo):
    resolver = EntityServiceResolver(spring_repo, FrameworkDetector.JAVA_SPRING)
    graph = resolver.build_dependency_graph()
    
    assert len(graph.entities) > 0
    assert len(graph.services) > 0
    assert len(graph.relationships) > 0
    
    # Every entity should have at least one service or be orphaned
    for entity, services in graph.relationships.items():
        # services is a list of service names using this entity
        pass
```

## Integration with Screen Mapping

The entity-service resolver enhances `ScreenMapper`:

```python
# Current approach: simple grep for entity references
tables = mapper._extract_db_tables(services)

# Enhanced approach: use resolver for deep analysis
resolver = EntityServiceResolver(repo_path, detected_framework)
for service in services:
    entities = resolver.find_entities_for_service(service)
    # Get complete set of entities (including indirect usage)
```

## Next Steps

1. **Implement** framework-specific resolvers for Java, .NET, Node
2. **Test** on real codebases (Spring Boot, ASP.NET Core, Express)
3. **Integrate** into `screen_mapper.py` for deeper entity-service linking
4. **Optimize** for performance on large codebases (caching, parallel)
5. **Extend** for additional frameworks (Go, Rust, Python/FastAPI)

See also:
- [CLAUDE.md § Entity Service Resolution](/home/user/ai-discovery/CLAUDE.md)
- [docs/guides/call-graph/](/home/user/ai-discovery/docs/guides/call-graph/)
