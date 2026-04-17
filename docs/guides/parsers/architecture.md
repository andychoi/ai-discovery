# Parser Architecture: Design & Implementation

This guide explains how parsers work in AI-Discovery and how tree-sitter is used for language-agnostic code analysis.

---

## Overview: Parser Architecture

Parsers convert source code into a structured graph of `CodeNode` objects that capture:
- Function/class definitions
- Call references (what functions call what)
- Data models (classes, fields)
- Entry points (HTTP endpoints, batch jobs, event listeners)
- Framework hints (what framework/library is this using?)

### Design Principle: Language Abstraction

All parsers implement the same interface (`LanguageParser`) so the pipeline can treat them uniformly:

```python
class LanguageParser(ABC):
    """Base parser interface for all languages"""
    
    @abstractmethod
    def parse(self, source_code: str, file_path: str) -> List[CodeNode]:
        """Parse source code and return CodeNode list"""
        pass
    
    @abstractmethod
    def get_calls(self, source_code: str) -> List[CallReference]:
        """Extract function call references"""
        pass
```

---

## Tree-sitter: The Foundation

### What is Tree-sitter?

**Tree-sitter** is a parser generator that builds fast, incremental parsers for any language using formal grammars. Key benefits:

| Feature | Benefit |
|---------|---------|
| **Fast** | Parses large files in milliseconds |
| **Incremental** | Can re-parse just the changed lines |
| **Language-agnostic** | Same API for all languages |
| **Robust** | Recovers from parse errors (useful for incomplete code) |
| **Available grammars** | Python, Java, C#, JavaScript, Go, Rust, and 50+ more |

### Tree-sitter AST Structure

```
SourceFile
├── ClassDeclaration (class Order)
│   ├── Identifier: "Order"
│   ├── FieldDeclaration (status: String)
│   │   ├── TypeIdentifier: "String"
│   │   └── Identifier: "status"
│   └── MethodDeclaration (void approve())
│       ├── Identifier: "approve"
│       └── Block
│           ├── ExpressionStatement
│           │   └── Assignment (this.status = "approved")
│           └── MethodInvocation
│               ├── Identifier: "save"
│               └── Arguments
```

Each node has:
- **type** (e.g., "method_declaration", "function_call")
- **text** (the actual source code)
- **start/end** (line/column positions)
- **children** (nested AST nodes)

---

## Parsing Workflow

### Phase 1: Tree-sitter Parse

```python
import tree_sitter as ts

# Load tree-sitter library + grammar
library = ts.Language("./build/languages.so", "python")
parser = ts.Parser()
parser.set_language(library)

# Parse source code
tree = parser.parse(source_code.encode())
root_node = tree.root_node
```

### Phase 2: AST Traversal

Walk the tree, identifying key nodes:

```python
def traverse_ast(node, source_code, code_nodes):
    if node.type == "class_definition":
        code_nodes.append(CodeNode(
            qualified_name=f"{module}.{get_identifier(node)}",
            node_type="class",
            source_code=get_source(node, source_code),
            ...
        ))
    
    elif node.type == "function_definition":
        code_nodes.append(CodeNode(
            qualified_name=f"{module}.{get_identifier(node)}",
            node_type="function",
            ...
        ))
    
    elif node.type == "call_expression":
        # Extract call reference
        callee_name = get_identifier(node.child_by_field_name("function"))
        call_references.append(CallReference(
            name=callee_name,
            file=file_path,
            line=node.start_point[0],
            ...
        ))
    
    # Recurse into children
    for child in node.children:
        traverse_ast(child, source_code, code_nodes)
```

### Phase 3: Call Reference Extraction

For each call expression, extract:
- **Function name** (callee)
- **Arguments** (how many, types if known)
- **Receiver** (object.method or module.function)
- **Imports** (what's imported at top of file)

```python
def extract_call_reference(call_node, source_code, imports):
    # Handle different call patterns:
    
    # Pattern 1: Direct function call
    # validate_order(items)
    if call_node.type == "call_expression":
        func_name = call_node.child_by_field_name("function").text
        return CallReference(name=func_name, receiver=None)
    
    # Pattern 2: Method call
    # order.validate()
    elif call_node.type == "member_expression":
        receiver = extract_receiver(call_node)
        method_name = call_node.child_by_field_name("property").text
        return CallReference(name=method_name, receiver=receiver)
    
    # Pattern 3: Module function call
    # validators.validate_order(items)
    elif "." in call_node.text:
        parts = call_node.text.split(".")
        return CallReference(name=parts[-1], receiver=".".join(parts[:-1]))
```

---

## Language-Specific Heuristics

Different languages have different AST structures and naming conventions. Parsers implement language-specific logic for:

### 1. Qualified Name Resolution

**Python**:
```python
# Module path: orders.validators
# Class: OrderValidator
# Method: validate_order

# Qualified name: orders.validators.OrderValidator.validate_order
qualified_name = f"{module}.{class_name}.{method_name}"
```

**Java**:
```java
// Package: com.company.orders.validators
// Class: OrderValidator
// Method: validateOrder

// Qualified name: com.company.orders.validators.OrderValidator.validateOrder
qualified_name = f"{package}.{class_name}.{method_name}"
```

**C#**:
```csharp
// Namespace: Company.Orders.Validators
// Class: OrderValidator
// Method: ValidateOrder

// Qualified name: Company.Orders.Validators.OrderValidator.ValidateOrder
qualified_name = f"{namespace}.{class_name}.{method_name}"
```

### 2. Entry Point Detection

**Python (Flask)**:
```python
@app.route('/api/orders', methods=['POST'])
def create_order():
    ...
# Entry point: POST /api/orders → create_order()
```

**Java (Spring)**:
```java
@PostMapping("/api/orders")
public Order createOrder(@RequestBody Order order) {
    ...
}
// Entry point: POST /api/orders → createOrder()
```

**JavaScript (Express)**:
```javascript
app.post('/api/orders', (req, res) => {
    ...
});
// Entry point: POST /api/orders → handler function
```

### 3. Framework Hints

Detect common frameworks and libraries:

```python
def extract_framework_hints(node, source_code, imports):
    hints = []
    
    # Check for decorators/annotations
    if has_decorator(node, "@route"):
        hints.append("flask.route")
    if has_decorator(node, "@RequestMapping"):
        hints.append("spring.mvc")
    if has_decorator(node, "@app.post"):
        hints.append("fastapi")
    
    # Check for framework-specific patterns
    if uses_method(node, "request.POST"):
        hints.append("django")
    if uses_method(node, "JSONObject"):
        hints.append("android.json")
    
    return hints
```

---

## Adding a New Parser: Template

### File Structure

```
app/parsers/
├── base.py             ← LanguageParser interface
├── python_parser.py    ← Python-specific implementation
├── go_parser.py        ← (To be added) Go-specific implementation
└── tests/
    ├── test_python_parser.py
    └── test_go_parser.py
```

### Minimal Implementation

```python
# app/parsers/go_parser.py

from tree_sitter import Language, Parser
from .base import LanguageParser, CodeNode, CallReference

class GoParser(LanguageParser):
    def __init__(self):
        self.language = Language("./build/languages.so", "go")
        self.parser = Parser()
        self.parser.set_language(self.language)
    
    def parse(self, source_code: str, file_path: str) -> List[CodeNode]:
        tree = self.parser.parse(source_code.encode())
        code_nodes = []
        
        # Traverse AST and extract CodeNodes
        self._traverse(tree.root_node, source_code, file_path, code_nodes)
        
        return code_nodes
    
    def get_calls(self, source_code: str) -> List[CallReference]:
        tree = self.parser.parse(source_code.encode())
        calls = []
        
        # Extract call references
        self._extract_calls(tree.root_node, source_code, calls)
        
        return calls
    
    def _traverse(self, node, source_code, file_path, code_nodes):
        """Recursively traverse AST and extract CodeNodes"""
        if node.type == "function_declaration":
            code_nodes.append(CodeNode(
                qualified_name=self._get_qualified_name(node),
                node_type="function",
                source_code=self._get_source(node, source_code),
                ...
            ))
        
        for child in node.children:
            self._traverse(child, source_code, file_path, code_nodes)
    
    def _extract_calls(self, node, source_code, calls):
        """Extract call references"""
        if node.type == "call_expression":
            calls.append(CallReference(
                name=self._get_function_name(node),
                line=node.start_point[0],
                ...
            ))
        
        for child in node.children:
            self._extract_calls(child, source_code, calls)
    
    def _get_qualified_name(self, node):
        """Go-specific: extract qualified name"""
        # Implementation: package.FunctionName or package.StructName.MethodName
        pass
    
    def _get_function_name(self, node):
        """Go-specific: extract function name from call"""
        # Implementation: FunctionName or receiver.MethodName
        pass
```

---

## Testing a New Parser

### Unit Test Template

```python
# app/parsers/tests/test_go_parser.py

import pytest
from app.parsers.go_parser import GoParser

def test_parse_function():
    """Test parsing a simple function"""
    code = """
    package main
    
    func sayHello(name string) {
        println("Hello " + name)
    }
    """
    
    parser = GoParser()
    nodes = parser.parse(code, "main.go")
    
    assert len(nodes) == 1
    assert nodes[0].qualified_name == "main.sayHello"
    assert nodes[0].node_type == "function"

def test_parse_struct_methods():
    """Test parsing struct and methods"""
    code = """
    package orders
    
    type Order struct {
        ID int
    }
    
    func (o *Order) Validate() bool {
        return o.ID > 0
    }
    """
    
    parser = GoParser()
    nodes = parser.parse(code, "order.go")
    
    assert len(nodes) == 2  # struct + method
    assert nodes[1].qualified_name == "orders.Order.Validate"

def test_extract_calls():
    """Test extracting call references"""
    code = """
    package orders
    
    func createOrder(items []Item) {
        validateItems(items)
        price := calculatePrice(items)
    }
    """
    
    parser = GoParser()
    calls = parser.get_calls(code)
    
    assert len(calls) == 2
    assert "validateItems" in [c.name for c in calls]
    assert "calculatePrice" in [c.name for c in calls]
```

---

## Language Patterns Reference

See `docs/guides/parsers/language-patterns.md` for AST differences between Python, Java, C#, JavaScript, and Go.

---

## Debugging a Parser

```python
# Debug: print AST
tree = parser.parse(source_code.encode())
print(tree.root_node)  # Pretty-print the tree

# Debug: find specific node types
def find_nodes(node, node_type):
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from find_nodes(child, node_type)

# Usage:
function_nodes = list(find_nodes(tree.root_node, "function_declaration"))
```

---

## See Also
- `docs/guides/parsers/extension-checklist.md` — Step-by-step guide to add a new parser
- `docs/guides/parsers/language-patterns.md` — AST patterns for different languages
- Tree-sitter docs: https://tree-sitter.github.io/
