# Test Projects for Screen-Centric Pipeline

This directory contains realistic, committed test projects used to validate the ai-discovery screen-centric documentation pipeline across multiple frameworks.

## Projects

### 1. Spring Boot Microservice (`spring-boot-app/`)
**Framework**: Java/Spring Boot  
**Size**: 14 screens, 3 entities, 3 repositories, 3 services, 3 controllers, 1 batch job

**Structure**:
```
spring-boot-app/
├── pom.xml                                    # Maven build config
├── src/
│   └── config/
│       └── menu.json                          # Menu definition (4 items → 14 screens)
│   └── main/java/com/example/
│       ├── entity/                            # JPA entities
│       │   ├── Product.java
│       │   ├── Order.java
│       │   ├── Customer.java
│       │   └── OrderItem.java
│       ├── repository/                        # JpaRepository interfaces
│       │   ├── ProductRepository.java
│       │   ├── OrderRepository.java
│       │   └── CustomerRepository.java
│       ├── service/                           # @Service classes
│       │   ├── ProductService.java
│       │   ├── OrderService.java
│       │   └── CustomerService.java
│       ├── controller/                        # @RestController classes
│       │   ├── ProductController.java
│       │   ├── OrderController.java
│       │   └── CustomerController.java
│       └── batch/
│           └── OrderExportJob.java            # @Scheduled batch job
```

**Detection Results**:
- Menu detection: ✅ 14 screens extracted
- Framework detection: ✅ Spring Boot (from pom.xml)
- Entity detection: ✅ 4 JPA @Entity classes
- Repository detection: ✅ 3 JpaRepository interfaces
- Service detection: ✅ 3 @Service classes with @Autowired
- Controller detection: ✅ 3 @RestController classes
- Batch job detection: ✅ @Scheduled method

### 2. ASP.NET Core API (`aspnet-core-app/`)
**Framework**: .NET 6.0 with Entity Framework  
**Size**: 5 screens, 2 entities, 1 service, 2 controllers

**Structure**:
```
aspnet-core-app/
├── EcommerceApi.csproj                       # .NET project config
├── config/
│   └── menu.json                              # Menu definition (2 items → 5 screens)
├── Models/                                    # Data models
│   ├── Product.cs
│   └── Order.cs
├── Services/                                  # Service classes
│   └── ProductService.cs
└── Controllers/                               # API controllers
    ├── ProductsController.cs
    └── OrdersController.cs
```

**Detection Results**:
- Menu detection: ✅ 5 screens extracted
- Framework detection: ✅ ASP.NET Core (from .csproj)
- Entity detection: ✅ 2 [Table] decorated classes
- Service detection: ✅ Constructor-injected services
- Controller detection: ✅ 2 [ApiController] classes

### 3. Express.js REST API (`express-app/`)
**Framework**: Node.js 14+, Express 4.x, Mongoose 6.x  
**Size**: 5 screens, 2 models, 2 services, 2 route handlers

**Structure**:
```
express-app/
├── package.json                               # npm config
├── config/
│   └── menu.json                              # Menu definition (2 items → 5 screens)
└── src/
    ├── models/                                # Mongoose models
    │   ├── Product.js
    │   └── Order.js
    ├── services/                              # Service classes
    │   ├── ProductService.js
    │   └── OrderService.js
    └── routes/                                # Express route handlers
        ├── products.js
        └── orders.js
```

**Detection Results**:
- Menu detection: ✅ 5 screens extracted
- Framework detection: ✅ Express (from package.json)
- Model detection: ✅ 2 mongoose.model() definitions
- Service detection: ✅ ES6 classes with module.exports
- Route detection: ✅ router.get/post/put/delete methods

## Usage in Tests

### Using Committed Projects
Tests now reference these committed projects instead of generating temporary ones:

**Before** (tempfile-based):
```python
@pytest.fixture
def spring_project():
    tmpdir = tempfile.mkdtemp()
    # ... 100+ lines of project creation code ...
    yield repo
```

**After** (committed fixtures):
```python
@pytest.fixture
def spring_project():
    return Path(__file__).parent / "projects" / "spring-boot-app"
```

### Running Tests Against Committed Projects
Update `tests/test_real_projects.py`:

```python
@pytest.fixture
def spring_project():
    project_path = Path(__file__).parent / "fixtures" / "projects" / "spring-boot-app"
    assert project_path.exists(), f"Project not found: {project_path}"
    return project_path
```

## Benefits of Committed Projects

✅ **Reproducibility**: Same project structure every test run  
✅ **Version Control**: Project changes tracked in git  
✅ **Review**: Reviewers can inspect test project architecture  
✅ **Performance**: No tempfile overhead in test setup  
✅ **Integration**: CI/CD can validate against real projects  
✅ **Documentation**: Projects serve as examples of framework usage  

## Maintenance

When updating projects:
1. Edit the appropriate project directory
2. Run tests to verify pipeline still works: `pytest tests/test_real_projects.py -v`
3. Commit changes with project updates

Example:
```bash
# Add new endpoint to Spring Boot controller
echo "
@GetMapping('/export')
public void exportProducts() { ... }
" >> src/main/java/com/example/controller/ProductController.java

# Test still passes
pytest tests/test_real_projects.py::TestSpringBootValidation -v

# Commit
git add tests/fixtures/projects/spring-boot-app/
git commit -m "Add export endpoint to ProductController"
```

## Framework Coverage

| Framework | Menu Items | Screens | Entities | Controllers | Services |
|-----------|-----------|---------|----------|------------|----------|
| Spring Boot | 4 | 14 | 4 | 3 | 3 |
| ASP.NET Core | 2 | 5 | 2 | 2 | 1 |
| Express.js | 2 | 5 | 2 | — | 2 |

All three frameworks demonstrate:
- ✅ Menu-to-screens extraction
- ✅ Framework detection accuracy
- ✅ Component pattern recognition
- ✅ Backend mapping capability

---

**Last Updated**: May 31, 2026  
**Total Projects**: 3  
**Total Test Scenarios**: 10+
