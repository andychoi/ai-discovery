# PM4Py Integration Guide: Stage 10.5 — Process Mining

This document describes how to integrate PM4Py process mining into the AI-Discovery pipeline as **Stage 10.5**, positioned after BPMN generation (Stage 10) and before markdown rendering (Stage 12).

- https://malinian.medium.com/unmasking-the-truth-process-mining-with-pm4py-6664c21e296f
---

## Overview

**Stage 10.5: Process Mining & Conformance Analysis**

```
Stage 10 (BPMN Generation)
       ↓
       pseudo_event_logs: dict[scenario_id] → { case_id, events[] }
       ↓
[Stage 10.5 — Process Mining]
  ├─ Discover: Inductive Miner → Petri Net
  ├─ Conform: Token Replay → fitness, precision, generalization
  ├─ Analyze: Bottleneck detection, edge frequency
  └─ Report: Markdown + JSON
       ↓
mining_results: dict[scenario_id] → MiningResult
       ↓
Stage 12 (Markdown Rendering)
```

---

## Architecture

### New Modules

1. **`app/ai/process_miner.py`** (90 lines)
   - `PseudoLogConverter` — Convert pseudo logs → PM4Py EventLog
   - `ProcessMiner` — Discover, analyze, report
   - `MiningResult` dataclass — Complete analysis output
   - `mine_scenarios()` — Batch mining function

2. **`app/ai/mining_reporter.py`** (280 lines)
   - `MiningReporter` — Generate markdown and JSON reports
   - Report sections: conformance, quality gates, bottlenecks, edges, diagnostics

### Data Models

```python
@dataclass
class MiningResult:
    scenario_id: str
    scenario_name: str
    domain: str | None
    
    # Discovery output
    net: Any  # PM4Py Petri Net
    initial_marking: Any
    final_marking: Any
    
    # Conformance
    conformance: ConformanceMetrics  # fitness, precision, generalization
    
    # Performance
    bottlenecks: list[BottleneckInfo]  # {activity, avg_ms, severity}
    edge_frequencies: list[EdgeFrequency]  # {from, to, count, percentage}
    avg_cycle_time_seconds: float
    
    # Quality
    fitness_threshold: float = 0.90
    fitness_passed: bool
    unmodeled_paths: list[str]
    warnings: list[str]
```

---

## Integration Steps

### Step 1: Import in Pipeline

In `app/pipeline.py`, add imports at the top:

```python
from .ai.process_miner import mine_scenarios
from .ai.mining_reporter import MiningReporter
```

### Step 2: Define Stage 10.5 Handler

Add a new function in `app/pipeline.py`:

```python
def _mine_processes(
    scenarios: list[Scenario],
    scenario_flows: dict[str, ScenarioFlow],
    output_dir: Path,
) -> dict[str, MiningResult]:
    """Stage 10.5: Process mining and conformance analysis.
    
    Args:
        scenarios: Scenario objects from ExecutionSliceBuilder
        scenario_flows: dict[scenario_id] → ScenarioFlow with pseudo logs
        output_dir: Directory for mining reports
    
    Returns:
        dict[scenario_id] → MiningResult
    """
    logger.info("Stage 10.5: Process mining and conformance analysis")
    
    # Extract pseudo event logs from scenario flows
    pseudo_logs = []
    for scenario_id, flow in scenario_flows.items():
        if hasattr(flow, 'pseudo_event_log'):
            pseudo_logs.append(flow.pseudo_event_log)
    
    if not pseudo_logs:
        logger.warning("No pseudo event logs available for mining")
        return {}
    
    # Mine all scenarios
    mining_results = mine_scenarios(pseudo_logs)
    
    # Save mining reports
    mining_reports_dir = output_dir / "mining_reports"
    mining_reports_dir.mkdir(parents=True, exist_ok=True)
    
    for scenario_id, result in mining_results.items():
        try:
            MiningReporter.save_reports(result, mining_reports_dir)
        except Exception as e:
            logger.error(f"Failed to save mining reports for {scenario_id}: {e}")
    
    logger.info(f"Stage 10.5 complete: {len(mining_results)} scenarios mined")
    
    return mining_results
```

### Step 3: Call in Main Pipeline

In the main `discover_scan()` function (in `app/pipeline.py`), after Stage 10 (BPMN generation), add:

```python
# Stage 10: BPMN and visual artifacts
scenario_flows = _generate_bpmn_artifacts(scenarios, ...)

# Stage 10.5: Process mining
mining_results = _mine_processes(scenarios, scenario_flows, output_dir)

# Persist mining results to DB
_persist_mining_results(mining_results, conn)

# Stage 12: Markdown rendering (now includes mining reports)
_render_markdown(domains, scenarios, mining_results, ...)
```

### Step 4: Database Table for Mining Results

Add to `app/db.py` schema:

```python
CREATE TABLE IF NOT EXISTS process_mining_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL UNIQUE,
    scenario_name TEXT,
    domain TEXT,
    
    -- Conformance metrics
    fitness REAL,
    precision REAL,
    generalization REAL,
    failed_traces_count INTEGER,
    total_traces_count INTEGER,
    
    -- Performance
    avg_cycle_time_seconds REAL,
    bottleneck_count INTEGER,
    
    -- Quality
    fitness_passed BOOLEAN,
    fitness_threshold REAL,
    
    -- Reports
    markdown_report TEXT,
    json_report TEXT,
    
    -- Metadata
    mined_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (scenario_id) REFERENCES scenarios(scenario_id)
);
```

### Step 5: Persist Mining Results

Add helper function in `app/pipeline.py`:

```python
def _persist_mining_results(mining_results: dict[str, MiningResult], conn) -> None:
    """Persist mining results to database."""
    cursor = conn.cursor()
    
    for scenario_id, result in mining_results.items():
        if not result.conformance:
            continue
        
        cursor.execute("""
            INSERT OR REPLACE INTO process_mining_results (
                scenario_id, scenario_name, domain,
                fitness, precision, generalization,
                failed_traces_count, total_traces_count,
                avg_cycle_time_seconds, bottleneck_count,
                fitness_passed, fitness_threshold,
                markdown_report, json_report,
                mined_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scenario_id,
            result.scenario_name,
            result.domain,
            result.conformance.fitness,
            result.conformance.precision,
            result.conformance.generalization,
            result.conformance.failed_traces_count,
            result.conformance.total_traces_count,
            result.avg_cycle_time_seconds,
            len(result.bottlenecks),
            result.fitness_passed,
            result.fitness_threshold,
            MiningReporter.generate_markdown_report(result),
            json.dumps(MiningReporter.generate_json_report(result)),
            result.mined_at,
        ))
    
    conn.commit()
    logger.info(f"Persisted {len(mining_results)} mining results")
```

### Step 6: Include Mining Reports in Markdown Rendering

In `app/output/doc_generator.py`, update `write_scenario_docs()`:

```python
def write_scenario_docs(
    scenarios: list[Scenario],
    scenario_flows: dict[str, ScenarioFlow],
    mining_results: dict[str, MiningResult],  # NEW
    output_dir: Path,
) -> None:
    """Render scenario documents including mining analysis."""
    
    for scenario in scenarios:
        flow = scenario_flows.get(scenario.scenario_id)
        if not flow:
            continue
        
        # Existing BPMN artifacts
        bpmn_xml = bpmn_gen.generate_bpmn_xml(flow)
        mermaid_seq = bpmn_gen.generate_mermaid_sequence(flow)
        plantuml = bpmn_gen.generate_plantuml(flow)
        ipo_md = bpmn_gen.generate_ipo_markdown(flow)
        
        # NEW: Mining analysis
        mining_result = mining_results.get(scenario.scenario_id)
        mining_md = ""
        if mining_result:
            mining_md = MiningReporter.generate_markdown_report(mining_result)
        
        # Combine into final doc
        doc_content = f"""# Process Flow: {flow.scenario_id}

## BPMN Diagram

```bpmn
{bpmn_xml}
```

## Mermaid Sequence

```mermaid
{mermaid_seq}
```

## Activity Diagram

```plantuml
{plantuml}
```

{ipo_md}

{mining_md}
"""
        
        # Write to file
        doc_path = output_dir / f"{scenario.scenario_id}_process_flow.md"
        doc_path.write_text(doc_content)
```

---

## Usage Example

### Command-line (once integrated)

```bash
# Standard scan with process mining enabled
discover scan ./my-repo -p myapp --mining

# Resume previous scan and re-mine with fresh logs
discover scan ./my-repo -p myapp --resume --mining --remine
```

### Programmatic (Python)

```python
from app.ai.process_miner import mine_scenarios
from app.ai.mining_reporter import MiningReporter

# Assume you have pseudo event logs from scenario flows
pseudo_logs = [
    {
        "case_id": "scenario_create_order_1",
        "events": [
            {"order": 1, "event_name": "Validate Order", "event_type": "PROCESS"},
            {"order": 2, "event_name": "Calculate Price", "event_type": "PROCESS"},
            {"order": 3, "event_name": "Save Order", "event_type": "DB"},
            {"order": 4, "event_name": "Publish Event", "event_type": "QUEUE"},
        ],
    }
]

# Mine
results = mine_scenarios(pseudo_logs)

# Report
for scenario_id, result in results.items():
    markdown = MiningReporter.generate_markdown_report(result)
    print(markdown)
    
    json_report = MiningReporter.generate_json_report(result)
    print(json.dumps(json_report, indent=2))
```

---

## Output Artifacts

### 1. Markdown Report: `{scenario_id}_mining_report.md`

Example output:

```markdown
# Process Mining Analysis: Order Creation Flow

**Scenario ID:** `scenario_create_order_1`
**Domain:** Order
**Mined:** 2026-04-16T14:32:18.123456

## Conformance Metrics

| Metric | Value |
|--------|-------|
| **Fitness** | 94.2% |
| **Precision** | 87.5% |
| **Generalization** | 91.0% |
| **Failed Traces** | 6 / 100 |

## Quality Gate: ⚠️ FAIL

⚠️ Model fitness (94.2%) below threshold (90.0%)

**Gap:** 5.8% of traces show unmodeled behavior

### Recommendations:
1. Review `alternate_paths` in scenario definition
2. Check for exception handling paths not captured
3. Verify gateway conditions in BPMN
...

## Bottleneck Analysis

### 1. Save Order 🔴

| Metric | Value |
|--------|-------|
| **Avg Duration** | 512 ms |
| **Median** | 480 ms |
| **Std Dev** | 380 ms |
| **Frequency** | 100 occurrences |

**Action:** Prioritize optimization...

...
```

### 2. JSON Report: `{scenario_id}_mining_report.json`

```json
{
  "scenario_id": "scenario_create_order_1",
  "scenario_name": "Order Creation Flow",
  "domain": "Order",
  "mined_at": "2026-04-16T14:32:18.123456",
  "conformance": {
    "fitness": 0.942,
    "precision": 0.875,
    "generalization": 0.91,
    "failed_traces_count": 6,
    "total_traces_count": 100
  },
  "cycle_time": {
    "avg_seconds": 1.234
  },
  "bottlenecks": [
    {
      "activity_name": "Save Order",
      "avg_duration_ms": 512.0,
      "median_duration_ms": 480.0,
      "std_dev_ms": 380.0,
      "max_duration_ms": 1850.0,
      "min_duration_ms": 45.0,
      "frequency": 100,
      "severity": "critical"
    }
  ],
  "edge_frequencies": [
    {
      "from_activity": "Validate Order",
      "to_activity": "Calculate Price",
      "count": 100,
      "percentage": 100.0
    }
  ],
  "quality_gate": {
    "fitness_threshold": 0.9,
    "fitness_passed": false
  },
  "warnings": [
    "Fitness 94.2% below threshold 90.0% (5.8% unmodeled)"
  ]
}
```

---

## Metrics Interpretation

### Fitness (94.2%)
- **Meaning:** 94.2% of event log traces can be replayed on the discovered model
- **Action if < 0.90:** Scenario has unmodeled paths; add to `alternate_paths`
- **Causes:** Missing error handling, concurrent branches, race conditions

### Precision (87.5%)
- **Meaning:** 87.5% of model behavior is reflected in the event log
- **Action if < 0.85:** Model is over-generalized; refine BPMN gateways
- **Causes:** Gateways allow paths not actually taken; incorrect conditions

### Generalization (91.0%)
- **Meaning:** Model's ability to handle unseen traces
- **Action if < 0.80:** Limited generalization; model may be too specific
- **Causes:** Too many rare edge cases; insufficient abstraction

### Bottlenecks
- **Severity:** critical (>2s avg OR >50% variance), high (>1s OR >30% var), medium, low
- **Action:** Profile slow activities; consider parallelization, caching, async

### Edge Frequency
- **100% frequency:** Mandatory edge (always taken)
- **80–99%:** Common path
- **< 50%:** Rare or conditional; verify against BPMN gateways

---

## PM4Py Algorithms: Comparison

| Algorithm | Pros | Cons | When to Use |
|-----------|------|------|-------------|
| **Inductive Miner** | Handles concurrency, clean Petri nets, good precision | Slower on large logs | **PRIMARY** (default) |
| **DFG Miner** | Fast, handles noise, good fitness | Over-generalization | Validation, quick check |
| **Alpha Miner** | Simple, classical | Poor with noise/skips | Baseline comparison |
| **Heuristics Miner** | Noise tolerant, frequency-aware | Slower, complex tuning | Messy/legacy code |
| **ILP Miner** | Optimal solution | Very slow, memory-intensive | Research only |

**Current implementation uses Inductive Miner by default.** Add config option to switch if needed:

```python
miner_variant = config.get("pm4py.miner_variant", "inductive")
# Support: inductive, dfg, alpha, heuristics
```

---

## Quality Gate Thresholds

Default thresholds (configurable):

```python
fitness_threshold = 0.90  # 90% must replay correctly
precision_threshold = 0.85  # 85% of model must be used
generalization_threshold = 0.80  # 80% generalization
```

If any threshold fails:
- **Stage status:** ⚠️ WARNING (not fatal)
- **DB record:** Mark `fitness_passed = false`
- **Markdown:** Include quality gate section with recommendations
- **Next step:** Manual review or re-mining with real logs

---

## Future Enhancements

### 1. Real Event Log Ingestion
Replace pseudo logs with actual runtime logs:
```python
# From OpenTelemetry, application logs, etc.
real_log = ingest_otel_traces(service_name="order-service")
mining_result = miner.mine_from_pm4py_log(real_log)
```

### 2. Comparative Mining
Compare multiple scenarios to detect patterns:
```python
aggregate_log = merge_logs([log1, log2, log3])
pattern = find_common_subprocesses(aggregate_log)
```

### 3. Drift Detection
Detect process changes over time:
```python
# Split log by date, mine each period
drifts = detect_concept_drift(log, window_size="1week")
```

### 4. Simulation & What-If
Use discovered model for simulation:
```python
simulation = pm4py.simulate(net, initial_marking, num_cases=10000)
```

---

## Troubleshooting

### PM4Py Installation
```bash
pip install pm4py
```

### Common Errors

**Error: `ImportError: No module named 'pm4py'`**
→ Install: `pip install pm4py`

**Error: `OSError: [Errno 28] No space left on device`**
→ PM4Py can be memory-intensive on very large logs. Consider:
- Sampling: Mine a subset of cases
- Filtering: Remove rare edges before mining
- Streaming: Process in chunks

**Error: `torch not found` (if using PM4Py ML features)**
→ Optional; mining works without it. Install if needed: `pip install torch`

---

## Configuration

Add to `discovery.yaml`:

```yaml
process_mining:
  enabled: true
  miner_variant: "inductive"  # inductive, dfg, alpha, heuristics
  fitness_threshold: 0.90
  precision_threshold: 0.85
  generalization_threshold: 0.80
  max_traces: 10000  # Limit for memory safety
  output_reports: true  # Generate markdown/JSON
  save_petri_nets: false  # Save .pnml files
```

---

## References

- **PM4Py Documentation:** https://pm4py.fit.fraunhofer.de/
- **Process Mining Papers:** https://en.wikipedia.org/wiki/Process_mining
- **Token Replay:** https://pm4py.fit.fraunhofer.de/documentation (search "token replay")
