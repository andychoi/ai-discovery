# Call Graph Resolution: Heuristics & Confidence Scoring

This guide explains how AI-Discovery resolves function calls and assigns confidence scores.

---

## The Problem

When parsing code, we find *call references* like:

```python
result = calculate_total(order, tax_rate)
```

But we don't immediately know which function this refers to. It could be:
- A local function in the same file
- A method in the same class
- An imported function from another module
- A dynamically loaded function
- An external API

This is the **call resolution problem**: given a function name and its context, find the actual target.

---

## Solution: 7-Level Confidence Scoring

Instead of answering "did we find it?" (boolean), we ask "how confident are we?" (0.5–1.0).

### The Seven Levels

| Level | Match Type | Confidence | Condition |
|-------|-----------|-----------|-----------|
| **1** | Exact | 1.0 | Fully qualified name match (same module, class, or namespace) |
| **2** | Class owner prefix | 0.95 | Caller and callee in same class (method-to-method call) |
| **3** | File local | 0.90 | Unique function in the same file as caller |
| **4** | Module local | 0.85 | Unique function in the same module/package |
| **5** | Suffix unique | 0.85 | Function name is unique globally; no prefix match needed |
| **6** | Prefix overlap | 0.65–0.75 | Best guess from namespace/prefix matching (ambiguous) |
| **7** | Unresolved / External | 0.50 | No local match found; likely external API or dynamic |

---

## Detailed Examples

### Level 1: Exact Match

```python
# File: orders/order_service.py
class OrderService:
    def create_order(self, items):
        self.validate_items(items)  # ← Call reference
        ...
    
    def validate_items(self, items):
        return len(items) > 0
```

**Call**: `self.validate_items(items)`  
**Resolution**: Exact match in same class  
**Confidence**: 1.0

---

### Level 2: Class Owner Prefix

```python
# File: orders/order_service.py (caller)
class OrderService:
    def create_order(self, items):
        price = self.calculate_price(items)  # ← Call reference (has self.)
```

**Call**: `self.calculate_price(items)`  
**Qualified name**: `orders.order_service.OrderService.calculate_price`  
**Resolution**: Method in same class; self-prefix eliminates ambiguity  
**Confidence**: 0.95

---

### Level 3: File Local (Unique in File)

```python
# File: orders/helpers.py
def validate_order_format(order):
    ...

class OrderValidator:
    def validate(self, order):
        if not validate_order_format(order):  # ← Call reference (no module prefix)
            raise ValueError("Invalid")
```

**Call**: `validate_order_format(order)`  
**Resolution**: Only one function named `validate_order_format` in this file  
**Confidence**: 0.90

---

### Level 4: Module Local (Unique in Module)

```python
# File: orders/order_service.py (caller)
from orders.helpers import validate_order_format

class OrderService:
    def create_order(self, items):
        validate_order_format(items)  # ← Call reference (imported at top)
```

**Call**: `validate_order_format(items)`  
**Resolution**: Imported; found in `orders.helpers` module; unique in module  
**Confidence**: 0.85

---

### Level 5: Suffix Unique

```python
# File: payments/payment_service.py (caller, no import!)
class PaymentService:
    def process_payment(self, order):
        result = charge_card(order.card)  # ← Call reference (no import visible)
```

```python
# File: billing/card_handler.py (somewhere else in codebase)
def charge_card(card):
    ...
```

**Call**: `charge_card(card)`  
**Resolution**: 
- Not in imports (no import statement found)
- Search codebase: "charge_card" is unique globally
- Confidence: We found it, but had to search broadly

**Confidence**: 0.85

---

### Level 6: Prefix Overlap (Ambiguous)

```python
# File: orders/order_service.py (caller)
class OrderService:
    def create_order(self, items):
        result = process_items(items)  # ← Call reference
```

Candidates in codebase:
1. `orders.item_handler.process_items` (same module prefix: `orders.`)
2. `item_management.processor.process_items` (different prefix)
3. `legacy.items.process_items` (different prefix)

**Call**: `process_items(items)`  
**Resolution**: 
- Exact match: Not found
- Class owner: N/A (not a method)
- File local: No
- Candidates with prefix overlap: `orders.item_handler.process_items` is the best
- But it's ambiguous (could be #2 or #3 if code is old/refactored)

**Confidence**: 0.70 (best guess, but not certain)

---

### Level 7: Unresolved / External

```python
# File: orders/order_service.py (caller)
import requests

class OrderService:
    def notify_user(self, user_id):
        response = requests.post(self.webhook_url, data=...)  # ← Call reference
```

**Call**: `requests.post(...)`  
**Resolution**: 
- External package (requests library)
- We know it's external, but don't analyze its internals
- Type: `EXTERNAL_API`

**Confidence**: 0.50 (we know it exists, but can't analyze it further)

---

## Signals Contributing to Confidence

Beyond simple matching, six signals influence confidence:

### Signal 1: Depth
Nodes closer to entry points are more likely in the primary execution path.

```
confidence += max(0, 5 - current_depth)
```

- Entry point (depth 0): +5
- One hop away (depth 1): +4
- Five hops away (depth 5): 0

**Why**: Business processes are modular; most important logic is near the entry point.

### Signal 2: State Transition
If a node changes state, it's significant to the business logic.

```
confidence += 4 if function_changes_entity_state
```

Example:
```python
def approve_order(order):
    order.status = OrderStatus.APPROVED  # ← State transition
    order.approved_at = datetime.now()
    order.save()
```

### Signal 3: Data Boundary
Database or queue interaction indicates the node is important.

```
confidence += 3 if node_type in (DB, QUEUE)
```

### Signal 4: External API Call
External calls are notable but less central.

```
confidence += 2 if node_type == EXTERNAL_API
```

### Signal 5: Read-After-Write
If a node reads data after writing it, suggests state dependency.

```
confidence += 3 if read_after_write_detected
```

### Normalized Score
```
score = (match_level + signal_bonuses) / 10
score = min(1.0, max(0, score))  # Clamp to [0, 1]
```

---

## When to Use Each Level

### High Confidence (≥ 0.9)
Use in:
- BPMN diagrams (safe to display to business users)
- Process flow documentation
- Scenario primary paths

Example: Level 1–3 calls (exact, class owner, file local)

### Medium Confidence (0.7–0.9)
Use in:
- Detailed technical docs (with confidence label)
- Flow diagrams with annotations
- Human review checklists

Example: Level 4–5 calls (module local, suffix unique)

### Low Confidence (< 0.7)
Flag for:
- Manual validation
- Alternate path suggestions
- Human review with domain knowledge

Example: Level 6–7 calls (prefix overlap, unresolved)

---

## Validating Confidence Scores

### Spot-Check Method

1. **Pick a low-confidence call** (< 0.7)
2. **Read the source code** at both ends
3. **Ask: Does the resolved target make sense?**
   - If yes: Accept the score
   - If no: Adjust the heuristic or mark as unresolved

### Systematic Validation

For a given codebase:
1. Extract all calls with confidence < 0.8
2. Manually validate a sample (10%)
3. Measure accuracy rate
4. If < 85% accuracy, adjust heuristics

### Example Validation Report

```markdown
## Validation: OrderService

Total calls: 145
Low-confidence calls (< 0.8): 23

Sample validation (5 calls):
- process_items() → orders.item_handler.process_items ✓ (correct, confidence 0.70)
- calculate_tax() → tax_calculator.Tax.calculate ✗ (wrong, should be billing.tax_service)
- validate_card() → payments.validator.validate_card ✓ (correct, confidence 0.65)
- notify_user() → [external: requests.post()] ✓ (correct, confidence 0.50)
- archive_order() → [unresolved] ⚠️ (might be external or dynamic)

Accuracy: 4/5 = 80% (below 85% threshold)

Recommendations:
- Increase confidence for module.function matches if imports are explicit
- Distinguish between "external" and "unresolved" more clearly
```

---

## When Heuristics Fail

### Scenario 1: Dynamic Dispatch
```python
handler = import_module(f"handlers.{handler_name}")
result = handler.process(data)  # ← Can't resolve at parse time
```

**Fix**: Mark as `EXTERNAL_API` or `UNRESOLVED` (0.5 confidence). Note: dynamic.

### Scenario 2: Monkey Patching
```python
from orders import order_service
order_service.create_order = custom_create_order  # ← Runtime replacement
```

**Fix**: Static analysis can't capture this. Mark as `UNRESOLVED` after seeing the assignment.

### Scenario 3: Factory Pattern
```python
def create_handler(type_name):
    if type_name == "email":
        return EmailHandler()
    elif type_name == "sms":
        return SMSHandler()
    return DefaultHandler()

handler = create_handler("email")
result = handler.send(message)  # ← Which handler?
```

**Fix**: Trace the factory; infer possible types (EmailHandler, SMSHandler, DefaultHandler). Generate alternate paths for each.

---

## Tuning Confidence Thresholds

In `discovery.yaml`:

```yaml
# Confidence thresholds for different uses
confidence:
  bpmn_diagram_min: 0.85     # Only include calls with confidence ≥ 0.85 in BPMN
  process_flow_min: 0.75     # Process flow can include medium-confidence calls
  human_review_min: 0.60     # Flag calls with confidence < 0.60 for review
  
  # Heuristic weights (used in scoring)
  depth_weight: 1.0
  state_transition_weight: 4.0
  data_boundary_weight: 3.0
  external_api_weight: 2.0
  read_after_write_weight: 3.0
```

---

## See Also
- `docs/guides/call-graph/debugging-workflow.md` — How to trace and debug a specific call
- `docs/guides/call-graph/test-strategy.md` — Validating confidence scores systematically
- `docs/architecture/decisions.md` — Why we chose 4-stage graded confidence
