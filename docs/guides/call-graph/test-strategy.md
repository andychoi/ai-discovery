# Call Graph Testing: Validation Strategy

This guide explains how to systematically test and validate call resolution heuristics.

---

## Why Test Call Resolution?

Call resolution heuristics are **load-bearing**. If confidence scoring is wrong, everything downstream is wrong:
- BPMN diagrams show incorrect flows
- LLM summaries miss important functions
- Execution scenarios are incomplete

Testing ensures heuristics stay accurate as code evolves.

---

## Test Strategy: Three Levels

### Level 1: Unit Tests (Heuristic Correctness)

Test individual match levels and signal scoring.

```python
# test_call_resolution.py

def test_exact_match():
    """Level 1: Exact qualified name match"""
    resolver = CallGraphResolver(code_nodes)
    call_ref = CallReference("validate_order", file="orders/order_service.py")
    target = resolver.resolve(call_ref)
    
    assert target.qualified_name == "orders.order_service.OrderService.validate_order"
    assert target.confidence == 1.0

def test_suffix_unique():
    """Level 5: Unique suffix match"""
    resolver = CallGraphResolver(code_nodes)
    call_ref = CallReference("process_payment", file="orders/order_service.py")
    # Assume process_payment is unique in codebase
    target = resolver.resolve(call_ref)
    
    assert target.confidence == 0.85
    assert target.match_level == 5

def test_signal_depth():
    """Signal: Depth bonus"""
    # Node at depth 0 (entry): +5
    # Node at depth 1: +4
    # Node at depth 5: 0
    assert calculate_depth_signal(depth=0) == 0.50  # (5-0) / 10
    assert calculate_depth_signal(depth=1) == 0.40
    assert calculate_depth_signal(depth=5) == 0.00

def test_signal_state_transition():
    """Signal: State transition bonus"""
    node_with_state_change = CodeNode(..., modifies_state=True)
    signal = calculate_signal_state_transition(node_with_state_change)
    assert signal == 0.40  # 4 / 10
```

### Level 2: Integration Tests (Heuristic Interaction)

Test heuristics working together on realistic code snippets.

```python
# test_call_resolution_integration.py

def test_order_service_scenario():
    """Integration: OrderService.create_order() call chain"""
    code = """
    class OrderService:
        def create_order(self, items):
            self.validate_items(items)           # Level 2: class owner (0.95)
            price = self.calculate_price(items)  # Level 2: class owner (0.95)
            self.save_to_db(...)                 # Level 2: class owner (0.95)
            notify_warehouse(order_id)           # Level 5: suffix unique (0.85)
            return order
    """
    
    resolver = CallGraphResolver.from_code(code)
    calls = resolver.get_all_calls()
    
    # Verify confidence distribution
    assert sum(1 for c in calls if c.confidence >= 0.90) >= 3  # Most calls are high-confidence
    assert sum(1 for c in calls if 0.80 <= c.confidence < 0.90) >= 1  # Some medium
    
    # Verify no calls are incorrectly resolved
    for call in calls:
        assert call.target.qualified_name in ["orders.order_service.OrderService.*", "*warehouse*"]

def test_cross_domain_scenario():
    """Integration: Calls across domains (orders → payments)"""
    code = """
    class OrderService:
        def create_order(self, items):
            total = self.calculate_price(items)
            result = charge_card(total)  # External domain call
            ...
    """
    
    resolver = CallGraphResolver.from_code(code)
    calls = resolver.get_all_calls()
    
    cross_domain = [c for c in calls if c.target.domain != "orders"]
    assert len(cross_domain) == 1
    assert cross_domain[0].confidence <= 0.85  # Cross-domain is harder
```

### Level 3: Validation Tests (Real Code)

Test against actual code repositories with known call chains.

```python
# test_call_resolution_validation.py

def test_django_orm_pattern():
    """Validation: Django ORM calls (common pattern)"""
    code = """
    from django.db import models
    
    class Order(models.Model):
        status = models.CharField(max_length=20)
        
        def approve(self):
            self.status = "approved"
            self.save()  # Django ORM method call
    
    order = Order.objects.get(id=1)  # Django queryapi
    order.approve()
    """
    
    resolver = CallGraphResolver.from_code(code, language="python", framework="django")
    calls = resolver.get_all_calls()
    
    # Verify ORM calls are recognized
    save_calls = [c for c in calls if c.name == "save"]
    assert len(save_calls) >= 1
    assert save_calls[0].target.framework_hint == "django.db.models"
    assert save_calls[0].confidence >= 0.80

def test_spring_dependency_injection():
    """Validation: Spring @Autowired calls (Java)"""
    code = """
    @Service
    public class OrderService {
        @Autowired
        private PaymentClient paymentClient;
        
        public void createOrder(Order order) {
            paymentClient.charge(order.getTotal());  // Dependency-injected call
        }
    }
    """
    
    resolver = CallGraphResolver.from_code(code, language="java", framework="spring")
    calls = resolver.get_all_calls()
    
    # Verify DI calls are resolved
    payment_calls = [c for c in calls if "charge" in c.name]
    assert len(payment_calls) >= 1
    assert payment_calls[0].target.qualified_name == "PaymentClient.charge"
```

---

## Batch Validation: Testing a Corpus

For systematic validation, use a **test corpus**—a set of real repositories with known call patterns.

### Step 1: Create Test Corpus

```
tests/fixtures/
├── django-rest-app/          # Sample Django application
│   ├── src/
│   │   ├── orders/
│   │   │   └── views.py
│   │   └── payments/
│   │       └── service.py
│   └── expected_calls.json   # Known call chains
├── spring-microservice/       # Sample Spring Boot app
│   ├── src/
│   ├── pom.xml
│   └── expected_calls.json
└── node-express-api/         # Sample Node/Express app
    ├── src/
    ├── package.json
    └── expected_calls.json
```

### Step 2: Define Expected Calls

For each corpus, document **known call chains** with expected confidence:

```json
{
  "django-rest-app": {
    "calls": [
      {
        "file": "orders/views.py",
        "line": 42,
        "function_called": "validate_order",
        "expected_target": "orders.validators.validate_order",
        "expected_confidence_min": 0.85,
        "expected_confidence_max": 1.0,
        "match_level": 4
      },
      {
        "file": "orders/views.py",
        "line": 50,
        "function_called": "charge_card",
        "expected_target": "payments.payment_service.charge_card",
        "expected_confidence_min": 0.65,
        "expected_confidence_max": 0.85,
        "match_level": 6
      }
    ]
  }
}
```

### Step 3: Run Corpus Validation

```python
# test_corpus_validation.py

def test_corpus_django_rest_app():
    """Validate against Django corpus"""
    corpus_dir = "tests/fixtures/django-rest-app"
    expected_calls = load_json(f"{corpus_dir}/expected_calls.json")
    
    resolver = CallGraphResolver.from_directory(f"{corpus_dir}/src")
    resolved_calls = resolver.resolve_all()
    
    # Compare resolved vs expected
    results = {
        "total": len(expected_calls),
        "correct": 0,
        "confidence_off": 0,
        "wrong_target": 0
    }
    
    for expected in expected_calls:
        resolved = find_call(resolved_calls, expected["file"], expected["line"])
        
        if resolved.target.qualified_name == expected["expected_target"]:
            results["correct"] += 1
        else:
            results["wrong_target"] += 1
        
        if expected["expected_confidence_min"] <= resolved.confidence <= expected["expected_confidence_max"]:
            pass  # Confidence is in range
        else:
            results["confidence_off"] += 1
    
    # Report
    accuracy = results["correct"] / results["total"]
    print(f"Accuracy: {accuracy:.0%}")
    print(f"Confidence off: {results['confidence_off']} calls")
    
    assert accuracy >= 0.85  # Target: 85% correct resolution
```

---

## Measuring Confidence Quality

### Metric 1: Resolution Accuracy

What % of resolved calls are correct?

```
accuracy = (correct_resolutions) / (total_resolutions)
target: >= 85%
```

### Metric 2: Confidence Calibration

When confidence = 0.85, how often is the resolution actually correct?

```python
# Group calls by confidence bucket
bins = {
  "0.9-1.0": [],
  "0.8-0.9": [],
  "0.7-0.8": [],
  "0.5-0.7": [],
}

for call in all_resolved_calls:
    bucket = find_confidence_bucket(call.confidence)
    bins[bucket].append(call.is_correct())

# Calculate accuracy per bucket
for bucket, calls in bins.items():
    accuracy = sum(calls) / len(calls)
    expected_confidence = float(bucket.split("-")[0])
    print(f"{bucket}: {accuracy:.0%} (expected: {expected_confidence:.0%})")
```

Good calibration: reported confidence ≈ actual accuracy.

### Metric 3: False Positive / False Negative Rate

**False Positive**: We resolved the call, but it's wrong.  
**False Negative**: We didn't resolve the call (0.50 confidence), but it exists.

```python
def test_fpr_fnr():
    """False Positive & False Negative Rate"""
    results = validate_corpus(corpus_dir)
    
    false_positives = [c for c in results if c.resolved and not c.correct]
    false_negatives = [c for c in results if not c.resolved and c.exists]
    
    fpr = len(false_positives) / len([c for c in results if c.resolved])
    fnr = len(false_negatives) / len([c for c in results if c.exists])
    
    print(f"False Positive Rate: {fpr:.1%}")
    print(f"False Negative Rate: {fnr:.1%}")
    
    assert fpr <= 0.10  # Max 10% of resolved calls are wrong
    assert fnr <= 0.15  # Max 15% of real calls are missed
```

---

## Regression Testing

When you change a heuristic, run regression tests to ensure you don't break existing accuracy.

```bash
# Baseline
python -m pytest tests/test_call_resolution_validation.py --corpus=django-rest-app --save-baseline

# Modify heuristic (e.g., adjust state_transition_weight)

# Regression test
python -m pytest tests/test_call_resolution_validation.py --corpus=django-rest-app --compare-baseline

# Output:
# ✓ Accuracy stable: 87% (baseline 87%)
# ✗ False Positive Rate increased: 12% (baseline 8%)  ← Regression!
```

---

## Checklist: Before Merging Call Resolution Changes

- [ ] Unit tests pass (all 7 match levels tested)
- [ ] Integration tests pass (realistic code scenarios)
- [ ] Corpus validation passes (85%+ accuracy on test fixtures)
- [ ] No regression (compare against baseline)
- [ ] Confidence calibration is good (reported ≈ actual accuracy)
- [ ] FPR and FNR are acceptable (FPR ≤ 10%, FNR ≤ 15%)
- [ ] Documentation updated (explain why the change improves heuristics)

---

## See Also
- `docs/guides/call-graph/resolution-heuristics.md` — Theory and examples
- `docs/guides/call-graph/debugging-workflow.md` — How to debug a specific call
- `docs/architecture/decisions.md` — Design rationale
