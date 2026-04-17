# Language-Specific AST Patterns

Reference guide for AST differences across languages supported by AI-Discovery.

---

## Python

### Module & Package Structure
```
Python:     module.submodule.ClassName.method_name
Example:    orders.validators.OrderValidator.validate
```

### AST Node Types
| Tree-sitter Type | AI-Discovery Type | Example |
|---|---|---|
| `class_definition` | class | `class Order:` |
| `function_definition` | function | `def validate(order):` |
| `decorated_definition` | function (with hints) | `@app.route('/orders')` |

### Call Expressions
```python
# Direct function call
validate(order)

# Method call
order.validate()

# Module function call
validators.validate(order)

# From import
from validators import validate
validate(order)  # Resolves to validators.validate
```

### Framework Patterns
```python
# Flask
@app.route('/api/orders', methods=['POST'])
def create_order():
    ...

# Django
from django.http import JsonResponse
def create_order(request):
    ...

# FastAPI
@app.post("/api/orders")
def create_order(order: Order):
    ...
```

### Entry Points
- HTTP handlers: Functions decorated with `@app.route`, `@app.post`, etc.
- Batch jobs: Functions defined in management commands
- Event listeners: Functions decorated with `@celery.task`

---

## Java

### Module & Package Structure
```
Java:       com.company.orders.validators.OrderValidator.validate
Package:    com.company.orders.validators
Class:      OrderValidator
Method:     validate
```

### AST Node Types
| Tree-sitter Type | AI-Discovery Type | Example |
|---|---|---|
| `class_declaration` | class | `class Order { }` |
| `interface_declaration` | interface | `interface OrderService { }` |
| `method_declaration` | method | `public void validate() { }` |

### Call Expressions
```java
// Direct method call (same class)
validate();  // → this.validate()

// Method call on object
order.validate();

// Static method call
OrderValidator.validate(order);

// Constructor call
new OrderValidator().validate(order);

// Super call
super.validate();
```

### Framework Patterns
```java
// Spring Boot
@Service
public class OrderService {
    @Autowired
    private PaymentClient paymentClient;
    
    @PostMapping("/api/orders")
    public Order create(@RequestBody Order order) {
        ...
    }
}

// Enterprise Java Beans (EJB)
@Stateless
public class OrderServiceBean {
    @EJB
    private PaymentService paymentService;
}
```

### Entry Points
- REST endpoints: Methods decorated with `@PostMapping`, `@GetMapping`, etc.
- Batch jobs: Classes extending `AbstractJobProcessingService`
- Message listeners: Classes implementing `MessageListener`

---

## C#

### Module & Package Structure
```
C#:         Company.Orders.Validators.OrderValidator.Validate
Namespace:  Company.Orders.Validators
Class:      OrderValidator
Method:     Validate
```

### AST Node Types
| Tree-sitter Type | AI-Discovery Type | Example |
|---|---|---|
| `class_declaration` | class | `class Order { }` |
| `interface_declaration` | interface | `interface IOrderService { }` |
| `method_declaration` | method | `public void Validate() { }` |
| `property_declaration` | property | `public string Name { get; set; }` |

### Call Expressions
```csharp
// Direct method call (same class)
Validate();  // → this.Validate()

// Method call on object
order.Validate();

// Static method call
OrderValidator.Validate(order);

// Constructor call
new OrderValidator().Validate(order);

// LINQ method call
orders.Where(o => o.Status == "active").FirstOrDefault();
```

### Framework Patterns
```csharp
// ASP.NET Core
[ApiController]
[Route("api/[controller]")]
public class OrderController : ControllerBase {
    [HttpPost]
    public async Task<IActionResult> Create([FromBody] Order order) {
        ...
    }
}

// Entity Framework
public class OrderContext : DbContext {
    public DbSet<Order> Orders { get; set; }
    protected override void OnModelCreating(ModelBuilder mb) {
        ...
    }
}
```

### Entry Points
- REST endpoints: Methods decorated with `[HttpPost]`, `[HttpGet]`, etc.
- Service methods: Classes implementing interfaces like `IOrderService`
- Background jobs: Classes implementing `IHostedService`

---

## JavaScript / TypeScript

### Module & Package Structure
```
JS/TS:      module.exports.functionName or export const functionName
Example:    validators.validateOrder or export function validateOrder()
```

### AST Node Types
| Tree-sitter Type | AI-Discovery Type | Example |
|---|---|---|
| `class_declaration` | class | `class Order { }` |
| `function_declaration` | function | `function validate() { }` |
| `arrow_function` | function | `const validate = () => { }` |
| `method_definition` | method | `validate() { }` (inside class) |

### Call Expressions
```javascript
// Direct function call
validate(order);

// Method call
order.validate();

// Imported function
import { validate } from './validators';
validate(order);

// Chained method calls
order.validate().sanitize().save();

// Constructor call
new OrderValidator().validate(order);

// Async function call
await validateOrder(order);
```

### Framework Patterns
```javascript
// Express
app.post('/api/orders', (req, res) => {
    const { order } = req.body;
    validateOrder(order);
    res.json({ success: true });
});

// NestJS
@Controller('api/orders')
export class OrderController {
    @Post()
    create(@Body() order: Order) {
        ...
    }
}

// Next.js API Route
export default async function handler(req, res) {
    if (req.method === 'POST') {
        const result = await validateOrder(req.body);
        res.status(200).json(result);
    }
}
```

### Entry Points
- Express routes: `app.post('/path', handler)`
- NestJS handlers: Methods decorated with `@Post`, `@Get`, etc.
- Next.js API routes: Functions in `pages/api/`
- Async functions called by event listeners

---

## Go

### Module & Package Structure
```
Go:         package.FunctionName or package.ReceiverType.MethodName
Example:    orders.CreateOrder or orders.Order.Validate
```

### AST Node Types
| Tree-sitter Type | AI-Discovery Type | Example |
|---|---|---|
| `type_declaration` | class | `type Order struct { }` |
| `function_declaration` | function | `func CreateOrder() { }` |
| `method_declaration` | method | `func (o *Order) Validate() { }` |

**Note**: Go doesn't have classes; `type` with struct is analogous.

### Call Expressions
```go
// Direct function call
CreateOrder(order)

// Method call
order.Validate()

// Package function call
validators.Validate(order)

// Interface method call (dynamic dispatch)
var validator OrderValidator
validator.Validate(order)  // Type determined at runtime

// Builtin function call
fmt.Println("Done")
```

### Framework Patterns
```go
// net/http
func (h *OrderHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    order := parseRequest(r)
    result := validateOrder(order)
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(result)
}

// Gin
func main() {
    r := gin.Default()
    r.POST("/api/orders", func(c *gin.Context) {
        var order Order
        c.BindJSON(&order)
        result := validateOrder(order)
        c.JSON(200, result)
    })
}

// GORM
func (repo *OrderRepository) Save(order *Order) error {
    return repo.db.Create(order).Error
}
```

### Entry Points
- HTTP handlers: Functions implementing `http.Handler` interface
- Handler functions passed to router (Gin, Echo, etc.)
- `func main()` entry point
- Goroutines started with `go` keyword

---

## Comparison Table: Function Declaration

| Language | Syntax | Qualified Name | Call Syntax |
|----------|--------|---|---|
| Python | `def validate(order):` | `module.validate` | `validate(order)` |
| Java | `void validate(Order order)` | `com.company.OrderService.validate` | `validate(order)` or `obj.validate(order)` |
| C# | `void Validate(Order order)` | `Company.Orders.OrderService.Validate` | `Validate(order)` or `obj.Validate(order)` |
| JavaScript | `function validate(order) { }` | `validators.validate` | `validate(order)` or `obj.validate(order)` |
| Go | `func Validate(order Order) { }` | `validators.Validate` | `Validate(order)` or `obj.Validate(order)` |

---

## Comparison Table: Class/Type Declaration

| Language | Syntax | Qualified Name | Method Call |
|----------|--------|---|---|
| Python | `class Order:` | `module.Order` | `order.validate()` |
| Java | `class Order { }` | `com.company.orders.Order` | `order.validate()` |
| C# | `class Order { }` | `Company.Orders.Order` | `order.Validate()` |
| JavaScript | `class Order { }` | `validators.Order` | `order.validate()` |
| Go | `type Order struct { }` | `validators.Order` | `order.Validate()` |

---

## Naming Conventions

| Language | Function Case | Class Case | Package/Module |
|----------|---|---|---|
| Python | `snake_case` | `PascalCase` | `snake_case` (files/folders) |
| Java | `camelCase` | `PascalCase` | `com.company.module` |
| C# | `PascalCase` | `PascalCase` | `Company.Module` |
| JavaScript | `camelCase` | `PascalCase` | `camelCase` or folder structure |
| Go | `PascalCase` (exported) | `PascalCase` | `lowercase` |

**Impact on call resolution**: When resolving calls, account for naming conventions. A call to `validate_order` (snake_case) in Python likely corresponds to a function named `validate_order`, not `validateOrder`.

---

## Database/ORM Patterns

| Language/Framework | Pattern | Example |
|---|---|---|
| Python/Django | ORM method call | `Order.objects.filter(status='approved')` |
| Python/SQLAlchemy | Session + query | `session.query(Order).filter(Order.status == 'approved')` |
| Java/Hibernate | Criteria/HQL | `session.createCriteria(Order.class)` |
| Java/JPA | EntityManager | `entityManager.createQuery("SELECT o FROM Order o")` |
| C#/EF Core | LINQ to Entities | `db.Orders.Where(o => o.Status == "approved")` |
| JavaScript/Sequelize | Query builder | `Order.findAll({ where: { status: 'approved' } })` |
| Go/GORM | Query builder | `db.Where("status = ?", "approved").Find(&orders)` |

**Framework hint detection**: When you see `query`, `filter`, `criteria`, or `Where`, mark as database interaction.

---

## Exception/Error Handling Patterns

| Language | Pattern | Example |
|---|---|---|
| Python | `try/except` | `try: validate(order) except ValidationError: ...` |
| Java | `try/catch` | `try { validate(order); } catch (ValidationException e) { }` |
| C# | `try/catch` | `try { Validate(order); } catch (ValidationException ex) { }` |
| JavaScript | `try/catch` | `try { await validate(order); } catch (err) { }` |
| Go | Error return | `if err := validate(order); err != nil { }` |

**Confidence impact**: Errors often indicate important business logic. Mark functions with error handling as potentially important.

---

## See Also
- `docs/guides/parsers/architecture.md` — How parsers work
- `docs/guides/parsers/extension-checklist.md` — Step-by-step guide to add a new language
