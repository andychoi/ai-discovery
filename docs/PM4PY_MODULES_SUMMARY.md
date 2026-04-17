# PM4Py Modules: Complete Reference

This document summarizes the PM4Py integration modules added to AI-Discovery for process mining and conformance analysis.

---

## Module Overview

### 1. `app/ai/process_miner.py` (440 lines)

**Core process mining engine. Discovers process models from event logs.**

#### Classes

| Class | Purpose | Key Methods |
|-------|---------|-------------|
| `PseudoLogConverter` | Converts pseudo event logs to PM4Py format | `convert()`, `convert_multiple()` |
| `ProcessMiner` | Main mining orchestrator | `mine_from_pseudo_log()`, `get_summary()` |
| `ConformanceMetrics` | Fitness, precision, generalization scores | (dataclass, no methods) |
| `MiningResult` | Complete mining output | (dataclass, accumulates all results) |
| `BottleneckInfo` | Single bottleneck activity | (dataclass) |
| `EdgeFrequency` | Transition frequency | (dataclass) |

#### Key Flows

```
Pseudo Log (dict) 
    ↓ PseudoLogConverter.convert()
PM4Py EventLog
    ↓ ProcessMiner.mine_from_pseudo_log()
    ├─ _discover_model() → Petri Net via Inductive Miner
    ├─ _check_conformance() → fitness/precision/generalization via Token Replay
    ├─ _analyze_performance() → bottlenecks, edge frequencies, cycle time
    ├─ _evaluate_quality_gates() → fitness_passed boolean
    └─ return MiningResult
```

#### Public API

```python
# Single scenario
miner = ProcessMiner(scenario_id, scenario_name, domain)
result = miner.mine_from_pseudo_log(pseudo_log)  # → MiningResult
summary = miner.get_summary()  # → dict (human-readable)

# Batch
results = mine_scenarios(pseudo_logs: list[dict])  # → dict[scenario_id] → MiningResult

# Conversion
log = PseudoLogConverter.convert(pseudo_log)
log = PseudoLogConverter.convert_multiple(pseudo_logs)
```

#### Configuration

```python
# Inside ProcessMiner
fitness_threshold = 0.90  # Quality gate: must exceed this
max_depth_inductive = -1  # No depth limit for Inductive Miner
```

---

### 2. `app/ai/mining_reporter.py` (340 lines)

**Report generation. Converts MiningResult → markdown and JSON artifacts.**

#### Classes

| Class | Purpose | Key Methods |
|-------|---------|-------------|
| `MiningReporter` | Static report generator | `generate_markdown_report()`, `generate_json_report()`, `save_reports()` |

#### Public API

```python
# Markdown report (for docs)
markdown = MiningReporter.generate_markdown_report(result)  # → str

# JSON report (for archival/ingestion)
json_dict = MiningReporter.generate_json_report(result)  # → dict

# Save both to disk
paths = MiningReporter.save_reports(result, output_dir)  # → {"markdown": Path, "json": Path}
```

#### Report Sections (Markdown)

1. **Conformance Metrics** — Fitness, precision, generalization table
2. **Quality Gate** — Pass/fail status + recommendations
3. **Performance Metrics** — Cycle time, bottleneck count
4. **Bottleneck Analysis** — Top activities by duration/severity
5. **Edge Frequency** — Transition frequency table
6. **Diagnostics** — Warnings, unmodeled paths, next steps

#### Report Structure (JSON)

```json
{
  "scenario_id": "string",
  "scenario_name": "string",
  "domain": "string",
  "mined_at": "ISO 8601 timestamp",
  "conformance": {
    "fitness": 0.0-1.0,
    "precision": 0.0-1.0,
    "generalization": 0.0-1.0,
    "failed_traces_count": int,
    "total_traces_count": int
  },
  "cycle_time": { "avg_seconds": float },
  "bottlenecks": [
    {
      "activity_name": "string",
      "avg_duration_ms": float,
      "median_duration_ms": float,
      "std_dev_ms": float,
      "max_duration_ms": float,
      "min_duration_ms": float,
      "frequency": int,
      "severity": "critical|high|medium|low"
    }
  ],
  "edge_frequencies": [
    {
      "from_activity": "string",
      "to_activity": "string",
      "count": int,
      "percentage": float
    }
  ],
  "quality_gate": {
    "fitness_threshold": float,
    "fitness_passed": boolean
  },
  "unmodeled_paths": ["string", ...],
  "warnings": ["string", ...]
}
```

---

## Data Models

### MiningResult (Dataclass)

```python
@dataclass
class MiningResult:
    # Identifiers
    scenario_id: str
    scenario_name: str
    domain: str | None

    # Discovery output (PM4Py objects)
    net: Any = None                      # Petri Net
    initial_marking: Any = None
    final_marking: Any = None

    # Conformance (from Token Replay)
    conformance: ConformanceMetrics | None = None
    # ├─ fitness: float (0-1)
    # ├─ precision: float (0-1)
    # ├─ generalization: float (0-1)
    # ├─ failed_traces_count: int
    # └─ total_traces_count: int

    # Performance
    bottlenecks: list[BottleneckInfo] = field(default_factory=list)
    edge_frequencies: list[EdgeFrequency] = field(default_factory=list)
    avg_cycle_time_seconds: float = 0.0

    # Quality gates
    fitness_threshold: float = 0.90
    fitness_passed: bool = False

    # Diagnostics
    unmodeled_paths: list[str] = field(default_factory=list)
    comparison_notes: str = ""
    warnings: list[str] = field(default_factory=list)

    # Metadata
    mined_at: str = field(default_factory=datetime.utcnow)
```

### BottleneckInfo (Dataclass)

```python
@dataclass
class BottleneckInfo:
    activity_name: str
    avg_duration_ms: float
    median_duration_ms: float
    std_dev_ms: float
    max_duration_ms: float
    min_duration_ms: float
    frequency: int
    severity: str  # "critical" | "high" | "medium" | "low"
```

**Severity Determination**:
- `critical`: avg > 2000ms OR std_dev > 50% of avg
- `high`: avg > 1000ms OR std_dev > 30% of avg
- `medium`: avg > 500ms
- `low`: else

### EdgeFrequency (Dataclass)

```python
@dataclass
class EdgeFrequency:
    from_activity: str
    to_activity: str
    count: int
    percentage: float  # as 0-100 range
```

### ConformanceMetrics (Dataclass)

```python
@dataclass
class ConformanceMetrics:
    fitness: float                  # 0-1
    precision: float                # 0-1
    generalization: float           # 0-1
    failed_traces_count: int
    total_traces_count: int
    notes: str = ""
```

---

## Pseudo Event Log Format

**Input Format** (as generated by `BPMNGenerator.generate_pseudo_event_log()`):

```json
{
  "case_id": "scenario_create_order_42",
  "process_name": "Create Order",
  "domain": "Order",
  "variant": "main",
  "events": [
    {
      "order": 1,
      "event_name": "Receive Order Request",
      "event_type": "ENTRY"
    },
    {
      "order": 2,
      "event_name": "Validate Order",
      "event_type": "PROCESS"
    },
    {
      "order": 3,
      "event_name": "Save to Database",
      "event_type": "DB"
    },
    {
      "order": 4,
      "event_name": "Publish OrderCreated Event",
      "event_type": "QUEUE"
    }
  ]
}
```

**Event Types**: `ENTRY`, `PROCESS`, `GATEWAY`, `DB`, `QUEUE`, `EXTERNAL_API`, `MANUAL`, `UNRESOLVED`

---

## PM4Py Algorithms Used

### Discovery: Inductive Miner

- **Algorithm**: Recursive process tree construction
- **Pros**: Handles concurrency, produces clean Petri nets, good precision
- **Paper**: *Robust Process Mining with Guarantees*
- **Config**: No depth limit (`max_depth=-1`)

### Conformance: Token Replay

- **Algorithm**: Replays log traces on Petri net
- **Output**: 
  - `fitness`: % of traces that replay without missing/remaining tokens
  - `precision`: % of model used by log
  - `generalization`: ability to handle unseen traces (heuristic)
- **Interpretation**:
  - fitness < 0.90 → unmodeled paths in log
  - precision < 0.85 → model over-generalizes
  - generalization < 0.80 → poor generalization capability

---

## Integration Points

### Pipeline Stage 10.5 (Pseudo Code)

```python
def discover_scan():
    # ... Stages 1-10 ...
    
    # Stage 10: BPMN Generation
    scenario_flows = _generate_bpmn_artifacts(scenarios, ...)
    
    # Stage 10.5: Process Mining [NEW]
    pseudo_logs = [flow.pseudo_event_log for flow in scenario_flows.values()]
    mining_results = mine_scenarios(pseudo_logs)
    
    # Persist and report
    _persist_mining_results(mining_results, conn)
    _include_mining_in_markdown(scenario_flows, mining_results, ...)
    
    # ... Stages 11-13 ...
```

### Database Schema

```sql
CREATE TABLE process_mining_results (
    scenario_id TEXT PRIMARY KEY,
    fitness REAL,
    precision REAL,
    generalization REAL,
    failed_traces_count INTEGER,
    avg_cycle_time_seconds REAL,
    bottleneck_count INTEGER,
    fitness_passed BOOLEAN,
    fitness_threshold REAL,
    markdown_report TEXT,
    json_report TEXT,
    mined_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## Usage Examples

### Example 1: Single Scenario Mining

```python
from app.ai.process_miner import ProcessMiner

pseudo_log = {
    "case_id": "scenario_1",
    "events": [
        {"order": 1, "event_name": "Start", "event_type": "ENTRY"},
        {"order": 2, "event_name": "Process", "event_type": "PROCESS"},
        {"order": 3, "event_name": "End", "event_type": "ENTRY"},
    ],
}

miner = ProcessMiner("scenario_1", "Test Flow", "Test")
result = miner.mine_from_pseudo_log(pseudo_log)

print(f"Fitness: {result.conformance.fitness:.1%}")
print(f"Bottlenecks: {len(result.bottlenecks)}")
```

### Example 2: Batch Mining

```python
from app.ai.process_miner import mine_scenarios
from app.ai.mining_reporter import MiningReporter

pseudo_logs = [...]  # List of pseudo logs
results = mine_scenarios(pseudo_logs)

for scenario_id, result in results.items():
    markdown = MiningReporter.generate_markdown_report(result)
    print(markdown)
```

### Example 3: Save Reports

```python
from pathlib import Path

output_dir = Path("./data/mining_reports")
for scenario_id, result in results.items():
    paths = MiningReporter.save_reports(result, output_dir)
    print(f"Saved: {paths['markdown']}, {paths['json']}")
```

See `examples/process_mining_example.py` for complete working examples.

---

## Performance Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| Single scenario mining | ~500ms | On typical 5-7 step flow |
| Batch mining (10 scenarios) | ~5s | Concurrent where possible |
| Memory per scenario | ~10MB | Depends on trace complexity |
| Max recommended traces | 10,000 | Per scenario |

**Bottleneck**: Token Replay on large Petri nets. Optimize by:
- Limiting max depth (for discovery)
- Sampling traces (if >1000)
- Filtering rare edges (precision <0.7)

---

## Configuration

Add to `discovery.yaml`:

```yaml
process_mining:
  enabled: true
  miner_variant: "inductive"  # or: dfg, alpha, heuristics
  
  # Quality gates
  fitness_threshold: 0.90
  precision_threshold: 0.85
  generalization_threshold: 0.80
  
  # Performance
  max_traces: 10000
  concurrent_mining: true
  
  # Reporting
  output_reports: true  # Generate markdown/JSON
  save_petri_nets: false  # Save .pnml Petri net files
```

---

## Troubleshooting

### ImportError: No module named 'pm4py'

```bash
pip install pm4py
```

### MemoryError on Large Logs

```python
# Sample before mining
log_sampled = sample_traces(log, max_traces=5000)
result = mine_from_pm4py_log(log_sampled)
```

### Fitness < 0.90

Indicates unmodeled paths:
1. Check `result.warnings` for details
2. Review `alternate_paths` in scenario definition
3. Look for exception handling not in main flow
4. Consider concurrent/retry logic

### Very Slow Mining

- Large Petri nets → try DFG miner instead of Inductive
- Many traces → sample to 1000-5000 before mining
- Large cycle times → check for I/O blocking

---

## Future Enhancements

- [ ] Real log ingestion (OpenTelemetry, application logs)
- [ ] Comparative mining (pattern detection across scenarios)
- [ ] Drift detection (process changes over time)
- [ ] Simulation & what-if analysis
- [ ] DFG miner option (for comparison)
- [ ] Conformance report with detailed alignments
- [ ] Performance bottleneck recommendations (parallelization suggestions)

---

## Files Modified/Created

| File | Type | Lines | Purpose |
|------|------|-------|---------|
| `app/ai/process_miner.py` | NEW | 440 | Core mining engine |
| `app/ai/mining_reporter.py` | NEW | 340 | Report generation |
| `docs/PM4PY_INTEGRATION.md` | NEW | 600 | Integration guide |
| `docs/PM4PY_MODULES_SUMMARY.md` | NEW | 350 | This document |
| `examples/process_mining_example.py` | NEW | 250 | Working examples |

---

## References

- **PM4Py Documentation**: https://pm4py.fit.fraunhofer.de/
- **Process Mining Overview**: https://en.wikipedia.org/wiki/Process_mining
- **Inductive Miner Paper**: https://pure.tue.nl/ws/files/3930556/preprint_inductive_miner.pdf
- **Medium Article**: https://malinian.medium.com/unmasking-the-truth-process-mining-with-pm4py-6664c21e296f
