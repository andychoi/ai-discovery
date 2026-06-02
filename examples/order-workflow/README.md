# order-workflow — control-flow-rich demo app

A small Python (FastAPI-style) order-checkout app **purpose-built to exercise
hierarchical processing-logic generation** (`ScenarioFlow.structured_steps`).

Unlike the CRUD fixtures under `tests/fixtures/projects/` (which have *zero*
conditionals, loops, or state transitions), this app deliberately contains:

- **Guarded state transitions** — `order.status` / `item.status` assigned inside
  `if/else` (produces `StateTransition.guard_expr` → DMN + GATEWAY arms).
- **Conditional branches** — risk flagging, pricing tier, payment outcome.
- **Loops over collections** — reserve stock per line item, sum line totals.
- **Multi-level call chains** — endpoint → `CheckoutService` →
  {`PricingService`, `InventoryService`, `PaymentGateway`, `OrderRepository`,
  `NotificationService`} (3–4 deep).
- **DB + external boundaries** — `repo.save()/commit()`, `requests.post()`.

## Scan it

```bash
# Requires an LLM backend (Tier-2 produces the leveled steps).
# Ollama (default per discovery.yaml) — start it and have the models pulled:
ollama serve &
discover scan examples/order-workflow -p order-workflow

# …or Bedrock:
discover scan examples/order-workflow -p order-workflow --provider bedrock
```

Then inspect a process-flow doc and look at its **Process** section / activity
diagram — they should now show nested phases, `if/else` branch arms with real
conditions, and `for each` loops, instead of a flat comma-joined list:

```bash
ls data/order-workflow/PF/
discover view -p order-workflow
```
