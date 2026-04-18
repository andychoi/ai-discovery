# Parser Extension: Step-by-Step Checklist

This checklist walks you through adding support for a new language to AI-Discovery.

---

## Overview: Adding a New Language (e.g., Go)

Expected time: 4–8 hours  
Scope: Full parser + test harness + documentation  
Difficulty: Intermediate (requires understanding tree-sitter AST)

---

## Phase 1: Pre-Flight Check (30 min)

### Step 1.1: Tree-sitter Grammar Available?

```bash
# Check if tree-sitter has a grammar for your language
# https://github.com/tree-sitter/tree-sitter/wiki/List-of-Parsers

# For Go: https://github.com/tree-sitter/tree-sitter-go
# Status: ✅ Maintained, production-ready

# For Rust: https://github.com/tree-sitter/tree-sitter-rust
# Status: ✅ Maintained, production-ready

# For your language:
[ ] Grammar exists
[ ] Grammar is maintained (last commit < 1 year)
[ ] Grammar supports language version you care about
```

### Step 1.2: Compile Tree-sitter Binary

```bash
# Install tree-sitter CLI
brew install tree-sitter  # macOS
# or: apt-get install tree-sitter-cli  # Linux

# Check your grammar is available
tree-sitter list-languages

# Output: [python, json, javascript, go, ...]
```

### Step 1.3: Collect Test Corpus (Real Code)

Gather 3–5 real-world repositories in the target language to test against.

```bash
mkdir -p tests/fixtures/go-corpus

# Example: Collect real Go projects
git clone https://github.com/prometheus/prometheus tests/fixtures/go-corpus/prometheus
git clone https://github.com/etcd-io/etcd tests/fixtures/go-corpus/etcd
git clone https://github.com/moby/moby tests/fixtures/go-corpus/docker
```

**Why**: 
- Validates parser works on real code (not toy examples)
- Establishes baseline for confidence scoring
- Helps identify framework-specific patterns

---

## Phase 2: AST Mapping (2–3 hours)

### Step 2.1: Understand Language AST Structure

```bash
# Use tree-sitter CLI to examine AST
tree-sitter parse tests/fixtures/go-corpus/prometheus/main.go

# Output (sample):
# source_file [0, 0] - [50, 0]
#   package_clause [0, 0] - [0, 14]
#     package_identifier: (identifier) [0, 8] - [0, 14]
#   import_declaration [2, 0] - [8, 1]
#     import_spec_list [2, 7] - [8, 1]
#       import_spec [3, 1] - [3, 14]
#         ...
#   function_declaration [10, 0] - [20, 1]
#     name: (identifier) [10, 5] - [10, 9]
#     parameters: (parameter_list) [10, 9] - [10, 11]
```

### Step 2.2: Map AST Node Types

Create a mapping of language AST types to AI-Discovery `CodeNode` types:

```python
# src/ai_discovery/parsers/go_parser.py

# AST Type Mapping for Go
AST_TYPE_TO_NODE_TYPE = {
    "function_declaration": "function",
    "method_declaration": "method",  # Note: Go doesn't have methods in traditional sense
    "type_declaration": "class",     # Struct is analogous to class
    "interface_declaration": "interface",
    "const_declaration": "constant",
    "var_declaration": "variable",
}

CALL_EXPRESSION_TYPES = {
    "call_expression": "direct_call",
}

ENTRY_POINT_PATTERNS = {
    "func main()": "main_entry",
    "func init()": "init_entry",
    "func (r *Router) ServeHTTP()": "http_handler",  # net/http interface
}
```

### Step 2.3: Test AST Mapping

```python
# test the mapping on a simple Go file

def test_ast_mapping():
    code = """
    package main
    
    import "fmt"
    
    func main() {
        sayHello("World")
    }
    
    func sayHello(name string) {
        fmt.Println("Hello " + name)
    }
    """
    
    parser = GoParser()
    nodes = parser.parse(code, "main.go")
    
    # Verify structure
    assert len(nodes) == 2  # main + sayHello
    assert nodes[0].qualified_name == "main.main"
    assert nodes[1].qualified_name == "main.sayHello"
    assert nodes[1].node_type == "function"
    
    print("✓ AST mapping correct")
```

---

## Phase 3: Call Reference Extraction (1–2 hours)

### Step 3.1: Identify Call Expression Patterns

```bash
# Examine call expressions in test corpus
tree-sitter parse tests/fixtures/go-corpus/prometheus/alert_handler.go | grep -A5 "call_expression"

# Output (sample):
# call_expression [15, 10] - [15, 25]
#   function: (identifier) [15, 10] - [15, 20]
#   arguments: (argument_list) [15, 20] - [15, 25]
```

### Step 3.2: Implement Call Extraction

```python
def extract_calls(self, node, source_code, calls):
    """Extract call references from Go AST"""
    
    if node.type == "call_expression":
        # Format: function_name(args) or receiver.method_name(args)
        func_node = node.child_by_field_name("function")
        
        # Handle direct calls: validate(order)
        if func_node.type == "identifier":
            calls.append(CallReference(
                name=func_node.text.decode(),
                receiver=None,
                line=node.start_point[0],
            ))
        
        # Handle method calls: order.Validate()
        elif func_node.type == "selector_expression":
            # selector: receiver.field
            receiver_node = func_node.child_by_field_name("operand")
            field_node = func_node.child_by_field_name("field")
            calls.append(CallReference(
                name=field_node.text.decode(),
                receiver=receiver_node.text.decode(),
                line=node.start_point[0],
            ))
    
    # Recurse
    for child in node.children:
        self.extract_calls(child, source_code, calls)
```

### Step 3.3: Test Call Extraction

```python
def test_call_extraction():
    code = """
    package main
    
    func main() {
        handler := NewOrderHandler()
        handler.Process(order)
        fmt.Println("Done")
    }
    """
    
    parser = GoParser()
    calls = parser.get_calls(code)
    
    assert len(calls) == 3
    call_names = [c.name for c in calls]
    assert "NewOrderHandler" in call_names
    assert "Process" in call_names
    assert "Println" in call_names
    
    print("✓ Call extraction correct")
```

---

## Phase 4: Qualified Name Resolution (1–2 hours)

### Step 4.1: Language-Specific Naming Conventions

```python
# Go: package.FunctionName or receiver.MethodName

def get_qualified_name(self, node, module_name=None):
    """Go-specific qualified name resolution"""
    
    # Get function/method name
    name_node = node.child_by_field_name("name")
    func_name = name_node.text.decode()
    
    # Check if it's a method (has receiver)
    params = node.child_by_field_name("parameters")
    if self._has_receiver(params):
        # Method: (receiver *Type) FuncName() -> receiver.FuncName
        receiver = self._extract_receiver(params)
        return f"{receiver}.{func_name}"
    else:
        # Function: package.FuncName
        if module_name:
            return f"{module_name}.{func_name}"
        else:
            return func_name
```

### Step 4.2: Import Resolution

```python
def extract_imports(self, source_code: str):
    """Extract import statements and build import map"""
    tree = self.parser.parse(source_code.encode())
    imports = {}
    
    def visit(node):
        if node.type == "import_spec":
            # Example: import "github.com/prometheus/client_golang/prometheus"
            # or: import prometheus "github.com/prometheus/client_golang/prometheus"
            
            # Extract package name and path
            package_name = self._extract_import_name(node)
            package_path = self._extract_import_path(node)
            imports[package_name] = package_path
        
        for child in node.children:
            visit(child)
    
    visit(tree.root_node)
    return imports
```

---

## Phase 5: Framework Detection (1–2 hours)

### Step 5.1: Identify Framework Patterns

```python
# Common Go frameworks and their patterns

FRAMEWORK_PATTERNS = {
    "net/http": {
        "patterns": [
            "func (.*) ServeHTTP",  # HTTP handler interface
            "func HandlerFunc",
        ],
        "imports": ["net/http"],
    },
    "gin": {
        "patterns": [
            r"func.*\(c \*gin\.Context\)",  # Gin handler signature
            r"router\.GET\(",
            r"router\.POST\(",
        ],
        "imports": ["github.com/gin-gonic/gin"],
    },
    "echo": {
        "patterns": [
            r"func.*\(c echo\.Context\)",
            r"e := echo\.New\(\)",
        ],
        "imports": ["github.com/labstack/echo"],
    },
}

def extract_framework_hints(self, source_code: str):
    """Detect frameworks used in code"""
    hints = set()
    
    # Check imports
    imports = self.extract_imports(source_code)
    for framework, config in FRAMEWORK_PATTERNS.items():
        for import_name in config["imports"]:
            if import_name in imports:
                hints.add(framework)
    
    # Check patterns
    for framework, config in FRAMEWORK_PATTERNS.items():
        for pattern in config["patterns"]:
            if re.search(pattern, source_code):
                hints.add(framework)
    
    return list(hints)
```

---

## Phase 6: Test Harness (1 hour)

### Step 6.1: Unit Tests

```python
# tests/test_go_parser.py

class TestGoParser:
    @pytest.fixture
    def parser(self):
        return GoParser()
    
    def test_parse_package_and_imports(self, parser):
        """Test parsing package declaration and imports"""
        code = """
        package main
        
        import (
            "fmt"
            prometheus "github.com/prometheus/client_golang/prometheus"
        )
        """
        nodes = parser.parse(code, "main.go")
        # Verify imports are captured
        
    def test_parse_functions(self, parser):
        """Test parsing function declarations"""
        code = """
        package main
        
        func add(a, b int) int {
            return a + b
        }
        """
        nodes = parser.parse(code, "main.go")
        assert len(nodes) == 1
        assert nodes[0].qualified_name == "main.add"
    
    def test_parse_methods(self, parser):
        """Test parsing struct methods"""
        code = """
        package orders
        
        type Order struct { ID int }
        
        func (o *Order) Validate() bool {
            return o.ID > 0
        }
        """
        nodes = parser.parse(code, "order.go")
        assert len(nodes) == 2
        assert nodes[1].qualified_name == "orders.Order.Validate"
    
    def test_extract_calls(self, parser):
        """Test call reference extraction"""
        code = """
        package main
        
        func main() {
            handler := NewHandler()
            handler.Process(data)
        }
        """
        calls = parser.get_calls(code)
        assert "NewHandler" in [c.name for c in calls]
        assert "Process" in [c.name for c in calls]
    
    def test_framework_detection(self, parser):
        """Test framework hint detection"""
        code = """
        package main
        
        import "github.com/gin-gonic/gin"
        
        func main() {
            r := gin.Default()
            r.GET("/api/orders", handleGetOrders)
        }
        """
        hints = parser.extract_framework_hints(code)
        assert "gin" in hints
```

### Step 6.2: Corpus Validation

```python
def test_parse_real_prometheus_code(parser):
    """Integration test: parse real Prometheus code"""
    prometheus_main = "tests/fixtures/go-corpus/prometheus/main.go"
    
    with open(prometheus_main, "r") as f:
        source = f.read()
    
    nodes = parser.parse(source, prometheus_main)
    
    # Should extract main function
    main_funcs = [n for n in nodes if n.qualified_name == "main.main"]
    assert len(main_funcs) >= 1
    
    # Should extract many functions
    assert len(nodes) > 10
    
    # Should find calls
    calls = parser.get_calls(source)
    assert len(calls) > 5
```

---

## Phase 7: Integration & Wire-Up (30 min)

### Step 7.1: Register Parser

```python
# src/ai_discovery/repo/lang_detector.py

LANGUAGE_PARSERS = {
    "python": PythonParser,
    "java": JavaParser,
    "csharp": CSharpParser,
    "javascript": JavaScriptParser,
    "go": GoParser,  # ← Add here
}

LANGUAGE_FILE_EXTENSIONS = {
    ".go": "go",
    ".py": "python",
    ...
}
```

### Step 7.2: Test Language Detection

```python
def test_detect_go():
    detector = LanguageDetector()
    
    files = ["main.go", "types.go", "handler.go"]
    detected = detector.detect_languages("tests/fixtures/go-corpus", files)
    
    assert detected["go"] >= 3
```

### Step 7.3: Run Full Pipeline on Test Corpus

```bash
discover scan \
    tests/fixtures/go-corpus/prometheus \
    --project-slug go-test \
    --output tests/output \
    --verbose

# Verify:
# - Parser loaded
# - Nodes extracted
# - Calls resolved
# - Documents generated
```

---

## Phase 8: Documentation (30 min)

### Step 8.1: Update `docs/guides/parsers/language-patterns.md`

Add a section documenting Go-specific AST patterns:

```markdown
## Go

### Qualified Names
- Function: `package.FunctionName`
- Method: `package.ReceiverType.MethodName`

### AST Node Types
| Tree-sitter Type | AI-Discovery Type | Example |
|---|---|---|
| function_declaration | function | func Add() |
| method_declaration* | method | func (o *Order) Validate() |
| type_declaration | class | type Order struct |

### Call Expression Patterns
- Direct: `FunctionName(args)`
- Method: `receiver.MethodName(args)`
- Package: `package.FunctionName(args)`

### Framework Hints
- `net/http`: Handler interface, ServeHTTP
- `gin`: gin.Engine, gin.Context parameter
- `gorm`: gorm.DB, Model interfaces
```

### Step 8.2: Update `docs/guides/parsers/architecture.md`

Add Go to the language examples section.

---

## Phase 9: Confidence Scoring Baseline (1 hour)

### Step 9.1: Measure Accuracy

```bash
python -m ai_discovery.debug.validate_calls \
    --db tests/output/go-test/discovery.db \
    --sample-size 50 \
    --output tests/validation-go.json
```

### Step 9.2: Record Baseline

```json
{
  "language": "go",
  "corpus": "prometheus",
  "calls_analyzed": 500,
  "accuracy": {
    "level_1_exact": 0.92,
    "level_2_class_owner": 0.00,  // N/A for Go
    "level_3_file_local": 0.85,
    "level_4_module_local": 0.78,
    "level_5_suffix_unique": 0.80,
    "overall": 0.82
  },
  "false_positive_rate": 0.05,
  "false_negative_rate": 0.12
}
```

**Target**: >= 80% overall accuracy before production use.

---

## Phase 10: Final Checklist

- [ ] Tree-sitter grammar available and compiled
- [ ] Test corpus collected (3–5 real repositories)
- [ ] AST mapping complete (function, method, class types)
- [ ] Call reference extraction working (direct, method, package calls)
- [ ] Qualified name resolution implemented (language-specific)
- [ ] Framework detection implemented (common frameworks)
- [ ] Unit tests passing (10+ test cases)
- [ ] Corpus validation passing (parse real code successfully)
- [ ] Integration tests passing (full pipeline on test corpus)
- [ ] Parser registered in lang_detector.py
- [ ] Documentation updated (language-patterns.md)
- [ ] Confidence scoring baseline recorded (>= 80% accuracy)
- [ ] No regressions on existing languages (Python, Java, C#, JS)

---

## Time Estimate Summary

| Phase | Duration | Effort |
|-------|----------|--------|
| Pre-flight | 30 min | ✓ Easy |
| AST mapping | 2–3h | ✓ Moderate |
| Call extraction | 1–2h | ✓ Moderate |
| Name resolution | 1–2h | ✓ Moderate |
| Framework detection | 1–2h | ✓ Moderate |
| Test harness | 1h | ✓ Easy |
| Integration | 30 min | ✓ Easy |
| Documentation | 30 min | ✓ Easy |
| Validation | 1h | ✓ Easy |

**Total: 8–13 hours** for a complete, production-ready parser.

---

## See Also
- `docs/guides/parsers/architecture.md` — How parsers work
- `docs/guides/parsers/language-patterns.md` — AST patterns for each language
