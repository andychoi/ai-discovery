# Call Graph Debugging: Step-by-Step Workflow

This guide walks you through debugging why a call resolution succeeded, failed, or scored unexpectedly.

---

## Quick Start: 3-Step Debugging

### Step 1: Identify the Suspect Call
```
File: app/parsers/python_parser.py
Line: 245
Call: validate_order(order_data)
Current confidence: 0.65
```

### Step 2: Run Debugging Tool
```bash
python -m app.debug.call_tracer \
  --file app/parsers/python_parser.py \
  --line 245 \
  --function validate_order
```

### Step 3: Review the Report
```
Tracing call: validate_order() at line 245

Candidates found: 2
  1. orders.validators.validate_order (match: suffix, confidence: 0.85)
  2. legacy.validator.validate_order (match: suffix, confidence: 0.85)

Resolution: Ambiguous. Chose #1 (alphabetical).
Confidence: 0.70 (ambiguous short-name)

Recommendation: Add explicit import to disambiguate.
```

---

## Full Debugging Workflow

### Phase 1: Trace the Call

**Inputs**:
- File path
- Line number (or function name + call site)

**Process**:
1. Load AST at the file
2. Find the call node at the line
3. Extract call reference (function name, arguments, context)
4. Check for explicit imports or receiver (object.method or module.function)

**Output**: Call node metadata
```python
{
  "file": "orders/order_service.py",
  "line": 42,
  "call_name": "validate_order",
  "context": "in method OrderService.create_order",
  "has_import": False,
  "has_receiver": False,  # not object.validate_order or module.validate_order
  "arguments": ["order"]
}
```

---

### Phase 2: Find Candidates

**Inputs**: Call node metadata

**Process**:
1. Check for explicit imports (Level 1–4 resolution)
2. Check same file (Level 3)
3. Check same module (Level 4)
4. Check unique global suffix (Level 5)
5. Check namespace prefix overlap (Level 6)
6. Mark as external/unresolved (Level 7)

**Output**: Candidate list with match types
```python
[
  {
    "candidate": "orders.validators.validate_order",
    "match_type": "suffix_unique",
    "match_level": 5,
    "base_confidence": 0.85,
    "signals": {
      "depth": 2,
      "state_transition": False,
      "data_boundary": False,
      "external_api": False,
      "read_after_write": False
    },
    "final_confidence": 0.80  # 0.85 + signals/10
  },
  {
    "candidate": "legacy.validator.validate_order",
    "match_type": "suffix_unique",
    "match_level": 5,
    "base_confidence": 0.85,
    "signals": { ... same ... },
    "final_confidence": 0.80
  }
]
```

---

### Phase 3: Analyze Signals

For each candidate, examine why it scored the way it did.

**Signal Breakdown Example**:
```
Candidate: orders.validators.validate_order
Confidence: 0.80

Signal Analysis:
  - Match level (suffix unique): +0.85
  - Depth (2 hops from entry): +0.20
  - State transition in function: No (0)
  - Data boundary (DB/Queue): No (0)
  - External API call: No (0)
  - Read-after-write: No (0)
  
  Total signal bonus: +0.20
  Final: 0.85 + 0.20/10 = 0.82 (clamped to 0.80)
```

**Ask**:
- Is this signal correct? (depth, state transition, etc.)
- Should other signals be added?
- Should weights be adjusted?

---

### Phase 4: Validate Against Source

**Manually verify**: Does the resolved call make sense?

**Checklist**:
- [ ] Function signature matches the call site (correct argument count/types)
- [ ] Function is reachable from the call site (not in a conditional branch that never executes)
- [ ] Function doesn't have side effects that would break the caller
- [ ] The resolved function's behavior aligns with the caller's expectations

**Examples**:

✓ **Correct Resolution**:
```python
# Call site: orders/order_service.py:42
validate_order(order_data)

# Resolved to: orders/validators.py
def validate_order(order):
    # Signature matches; behavior aligns
    return order.items and order.total > 0
```

✗ **Incorrect Resolution**:
```python
# Call site: orders/order_service.py:42
validate_order(order_data)

# Resolved to: auth/validators.py (WRONG!)
def validate_order(api_key):
    # Signature doesn't match; different purpose
    return api_key.startswith("key_")
```

⚠️ **Ambiguous**:
```python
# Call site: payments/payment_service.py:100
process_payment(order)

# Multiple candidates found:
# 1. payments.processor.process_payment (signature matches, same module)
# 2. order.processor.process_payment (signature matches, different domain)
# Chose #1, but confidence is only 0.70 (ambiguous)
```

---

### Phase 5: Decide: Accept or Refine

After validation, decide:

**Option A: Accept the Score**
- Resolution is correct (or good enough)
- Confidence score is reasonable
- Document the validation result

**Option B: Adjust Heuristics**
- Signal weight is wrong (e.g., state_transition should be more important)
- Match level is wrong (e.g., suffix_unique should be 0.90, not 0.85)
- Missing signal (e.g., "has_explicit_import" should boost confidence)

**Option C: Mark as Unresolved**
- Dynamic dispatch, external, or truly ambiguous
- Change confidence to 0.50 (UNRESOLVED or EXTERNAL_API)
- Document why

**Option D: Refactor Code**
- Add explicit import to disambiguate
- Add type hints so static analysis works better
- Break apart large modules where many functions have the same name

---

### Phase 6: Document Findings

Record the decision in a debugging report:

**Example Report**:

```markdown
## Call Resolution Debugging Report

### Call: validate_order() in orders/order_service.py:42

**Initial State**:
- Confidence: 0.65 (ambiguous)
- Candidates: 2 (suffix_unique match)

**Analysis**:
- Both candidates have identical signatures
- Caller doesn't provide enough context to disambiguate
- Module prefix suggests `orders.validators` is more likely (same domain)

**Validation**:
- ✓ Signature matches: `validate_order(order) -> bool`
- ✓ Function exists: `orders/validators.py:10`
- ✓ Behavior aligns: Validates order fields (items, total)
- ⚠️ Ambiguous: Second candidate `legacy.validator.validate_order` also valid

**Decision**: Accept score 0.70, flag for manual review.

**Recommendation**: Add explicit import:
```python
from orders.validators import validate_order  # Disambiguate
```

### Related Calls
- [ ] Other calls in this file
- [ ] Other calls in this module
- [ ] Other uses of validate_order() elsewhere
```

---

## Advanced: Tracing Chains

When debugging a scenario or business flow, you may want to trace multiple calls:

**Example**: OrderService.create_order() calls process_order() which calls calculate_price()

**Workflow**:
1. Trace call #1: `process_order()` at line 42
   - Resolved to: `orders.processor.process_order` (confidence 0.95)
2. Trace call #2: `calculate_price()` at line 75 (inside resolved process_order())
   - Resolved to: `billing.price_calculator.calculate_price` (confidence 0.70)
3. Trace call #3: `apply_discount()` at line 100 (inside calculate_price())
   - Resolved to: `promotions.discount_service.apply_discount` (confidence 0.60)

**Report Chain**:
```
OrderService.create_order()
  └─ process_order() [confidence: 0.95]
       └─ calculate_price() [confidence: 0.70] ⚠️
            └─ apply_discount() [confidence: 0.60] ⚠️⚠️
```

**Action**: Focus on the low-confidence calls at the end of the chain.

---

## Debugging Tools (Reference)

### Command-Line Tracer
```bash
python -m app.debug.call_tracer \
  --file app/parsers/python_parser.py \
  --line 245 \
  --function validate_order \
  --show-signals \
  --show-candidates
```

### Interactive REPL
```python
from app.graph.call_graph import CallGraphDebugger

debugger = CallGraphDebugger(code_graph)
call_info = debugger.trace_call(file_path="orders/order_service.py", line=42)
debugger.print_candidates(call_info)
debugger.validate_candidate(call_info.candidates[0], source_code)
```

### Batch Validation
```bash
python -m app.debug.validate_calls \
  --db data/discovery.db \
  --min-confidence 0.5 \
  --max-confidence 0.8 \
  --sample-size 20
```

---

## Troubleshooting Common Issues

### Issue 1: Too Many Candidates (High Ambiguity)

**Symptom**: 5+ candidates with similar confidence scores

**Causes**:
- Function name is generic (process, handle, validate)
- Code is poorly organized (same functions in multiple modules)
- Missing type information (Python: add type hints)

**Fixes**:
- Increase match_level threshold (only Level 1–4, skip Level 5–6)
- Add explicit imports in code
- Add type hints (Python) or overload resolution (Java)

### Issue 2: Unresolved Calls (Low Confidence)

**Symptom**: Most calls are 0.5–0.6 confidence

**Causes**:
- Code uses dynamic dispatch (reflection, monkey patching)
- External dependencies aren't analyzed
- Parser doesn't understand language-specific call patterns

**Fixes**:
- Mark as `EXTERNAL_API` or `UNRESOLVED` (accept 0.5)
- Add manual annotations in config.yaml
- Extend parser to handle language idioms

### Issue 3: Wrong Resolutions (Incorrect Candidates)

**Symptom**: Validation shows wrong function was selected

**Causes**:
- Heuristic weights are wrong (state_transition_weight too low)
- Match level is wrong (suffix_unique should be higher priority)
- Parser missed an import statement

**Fixes**:
- Review and adjust heuristic weights
- Verify parser extracts all imports
- Add explicit test cases for this code pattern

---

## See Also
- `docs/guides/call-graph/resolution-heuristics.md` — Theory behind 7-level scoring
- `docs/guides/call-graph/test-strategy.md` — Systematic validation approach
- `docs/architecture/decisions.md` — Why we chose multi-signal confidence
