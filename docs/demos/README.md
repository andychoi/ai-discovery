# AI-Discovery Demo: TodoApp

Discover a real C#/.NET application using MLX Qwen3.5 models on mlc-server.

**Repository**: [davidfowl/TodoApp](https://github.com/davidfowl/TodoApp)  
**Models**: Qwen3.5-2B (tier1), 4B (tier2), 9B (tier3)  
**Provider**: mlc-server (local LLM)  
**Runtime**: 1-3 hours (depending on options)

---

## Quick Start

### 1. Start mlc-server

```bash
# Terminal 1: Start MLC server with Qwen3.5-2B
ollama serve mlx-community/Qwen3.5-2B-4bit

# Or: Download if not present
ollama pull mlx-community/Qwen3.5-2B-4bit
ollama serve mlx-community/Qwen3.5-2B-4bit
```

### 2. Run Discovery

```bash
# Terminal 2: Run the demo
cd /path/to/ai-discovery
./demo.sh
```

### 3. Review Results

```bash
# Browse generated documentation
ls -la data/todoapp/docs/
cat data/todoapp/docs/ASIS/*.md        # Domain overview
cat data/todoapp/docs/PF/*.md          # Process flows
cat data/todoapp/mining_reports/*.md   # Mining analysis (if enabled)
```

---

## Usage Options

### Full Discovery (Default)
```bash
./demo.sh
# Runs: Parsing → Summarization → Flow analysis → BPMN → Mining → Self-review
# Time: 2-3 hours
```

### Skip Process Mining (Faster)
```bash
./demo.sh --no-mining
# Skips Phase 13.6 (process mining)
# Time: 1.5-2 hours
# Output: No mining_reports/
```

### Skip Self-Review (Quick Demo)
```bash
./demo.sh --quick
# Skips Stage 14 (claim verification)
# Time: 30-45 minutes
# Note: Docs may have unverified claims
```

### Force Rescan
```bash
./demo.sh --rescan
# Ignores previous run cache, re-scans everything
# Time: 2-3 hours
```

### Show Help
```bash
./demo.sh --help
# Displays full help and configuration details
```

---

## What Gets Discovered

The TodoApp is a .NET Core/C# application with:

- **HTTP APIs** (ASP.NET Core endpoints)
- **Data Models** (Entity Framework, SQL)
- **Business Logic** (order processing, validation, etc.)
- **External Services** (database, event queues)
- **Process Flows** (creation, approval, cancellation workflows)

### Output Structure

```
data/todoapp/
├── docs/
│   ├── ASIS/                  # As-is domain documentation
│   ├── ASD/                   # Detailed specs
│   ├── SPEC/                  # Technical specifications
│   ├── DM/                    # Data model docs
│   ├── IF/                    # Interface definitions
│   └── PF/                    # Process flows (BPMN, Mermaid, PlantUML)
├── mining_reports/            # Process mining analysis (if enabled)
│   ├── scenario_*.md          # Markdown reports
│   └── scenario_*.json        # JSON metrics
├── discovery.log              # Full execution log
└── discovery-todoapp.db       # SQLite cache (resume-able)
```

---

## Pipeline Stages

| Stage | Time | Purpose |
|-------|------|---------|
| 1-9 | 10-15m | Parse code, build graph, chunk, embed |
| 11 | 10-20m | Tier 1: Summarize chunks (2B model) |
| 12 | 15-30m | Tier 2: Flow analysis (4B model) |
| 12.5 | 5-10m | Infer scenario flows from slices |
| 13.5 | 5-10m | Generate BPMN, Mermaid, PlantUML |
| **10.5** | **10-20m** | **Process mining (optional)** |
| 15 | 5m | Render markdown files |
| **14** | **30-60m** | **Self-review (optional)** |
| **Total** | **2-3h** | **Full discovery** |

---

## Configuration

The demo creates `data/todoapp/discovery.yaml` with sensible defaults:

```yaml
provider: mlx-qwen

mlx_qwen:
  base_url: http://localhost:11435
  tier1: mlx-community/Qwen3.5-2B-4bit        # Fast
  tier2: mlx-community/Qwen3.5-4B-MLX-4bit   # Standard
  tier3d: mlx-community/Qwen3.5-9B-MLX-4bit  # Dev

process_mining:
  enabled: false  # Set to true to enable
  fitness_threshold: 0.90
```

### To Enable Process Mining

Edit `data/todoapp/discovery.yaml`:

```yaml
process_mining:
  enabled: true  # ← Change from false
```

Then rerun: `./demo.sh --rescan`

---

## Example Output

### Process Flow (Markdown)

```markdown
# Process Flow: Create Order

## Business Steps

1. Receive Order Request (ENTRY)
2. Validate Order (PROCESS)
3. Calculate Price (PROCESS)
4. Check Inventory (DB)
5. Save Order (DB)
6. Publish Order Created Event (QUEUE)
7. Return Order Response (ENTRY)

## Sequence Diagram

[Mermaid sequence showing HTTP → Validate → DB → Queue]

## BPMN Diagram

[BPMN XML with swimlanes, gateways, pools]

## IPO Table

| Input | Process | Output |
|-------|---------|--------|
| OrderRequest | Validate | OrderId |
| ... | ... | ... |

## Process Mining Analysis (if enabled)

**Fitness**: 94.0% (6% unmodeled paths)
**Precision**: 87.5%
**Generalization**: 91.0%

Bottlenecks:
- Save Order: 512ms avg
- Publish Event: 380ms avg
```

### Domain Doc (As-Is)

```markdown
# Order Service (As-Is)

**Domain**: Order  
**Language**: C#  
**Framework**: ASP.NET Core  

## Responsibilities

- Receive and validate customer orders
- Calculate pricing and taxes
- Check inventory availability
- Persist to database
- Publish order events

## Key Classes

- `OrderController` - HTTP endpoint
- `OrderService` - Business logic
- `OrderRepository` - Data access
- `PricingService` - Price calculation

## Dependencies

- SQL Database (Entity Framework)
- RabbitMQ (order events)
- Inventory Service (API)

## Quality Metrics

- Fitness: 94.0% (with process mining)
- Test Coverage: [from code analysis]
- Complexity: Moderate
```

---

## Troubleshooting

### MLC Server Not Responding

```bash
# Error: "MLC Server is not responding on http://localhost:11435"

# Solution 1: Start mlc-server
ollama serve mlx-community/Qwen3.5-2B-4bit

# Solution 2: Check if ollama is installed
ollama -v

# Solution 3: Check port
lsof -i :11435
```

### Out of Memory

```bash
# Error: "CUDA out of memory"

# Solution: Reduce model size
# Edit data/todoapp/discovery.yaml:
mlx_qwen:
  tier1: mlx-community/Qwen3.5-1.5B-4bit  # Smaller
  tier2: mlx-community/Qwen3.5-2B-4bit    # Smaller
  tier3d: mlx-community/Qwen3.5-4B-MLX-4bit
```

### Discovery Takes Too Long

```bash
# Option 1: Skip mining
./demo.sh --no-mining

# Option 2: Skip self-review
./demo.sh --quick

# Option 3: Both
./demo.sh --no-mining --quick

# Option 4: Reduce max_concurrent in discovery.yaml
max_concurrent: 2  # From 4
```

### Resume Previous Run

```bash
# If discovery was interrupted:
./demo.sh
# It will resume from the checkpoint

# To force full rescan:
./demo.sh --rescan
```

---

## Performance Tips

### Recommended Hardware

- **GPU**: 6GB+ VRAM (for 9B model)
- **CPU**: 8+ cores
- **RAM**: 16GB+
- **Storage**: 10GB for repo + output

### Optimize Speed

1. **Reduce concurrency**: `max_concurrent: 2` (instead of 4)
2. **Skip mining**: `./demo.sh --no-mining`
3. **Skip self-review**: `./demo.sh --quick`
4. **Smaller models**: Use 2B tier1, 4B tier2
5. **Limit chunk processing**: See `rag.chunk_size` in config

### Monitor Progress

```bash
# Watch logs in real-time
tail -f data/todoapp/discovery.log

# Check generated documents
watch -n 5 'find data/todoapp/docs -name "*.md" | wc -l'

# Monitor model memory
watch -n 2 'nvidia-smi'  # If using NVIDIA GPU
```

---

## What Gets Analyzed

### Code

- C# source files (controllers, services, models)
- Entity Framework migrations
- API endpoints
- Business logic
- Database schemas

### Artifacts Generated

- **As-Is Documentation**: Domain overview, responsibilities, dependencies
- **Detailed Specs**: Class diagrams, API contracts, data models
- **Process Flows**: BPMN, Mermaid sequence, PlantUML activity
- **Mining Reports**: Conformance metrics, bottlenecks, edge frequencies

### Confidence Scores

Each document includes confidence metrics:
- 0.0-0.33: Low (speculative, needs verification)
- 0.34-0.66: Medium (validated with some reservations)
- 0.67-1.0: High (well-supported by code)

---

## Next Steps

### 1. Review Documentation

```bash
# Start with domain overview
cat data/todoapp/docs/ASIS/*.md | less

# Check process flows
open data/todoapp/docs/PF/scenario-*.md

# Review mining analysis (if enabled)
cat data/todoapp/mining_reports/*.md
```

### 2. Enable Process Mining

Edit `data/todoapp/discovery.yaml`:

```yaml
process_mining:
  enabled: true
```

Rerun: `./demo.sh --rescan`

### 3. Integrate into CI/CD

```bash
# Add to your CI/CD pipeline
discover scan . -p myapp -c discovery.yaml

# Gate on quality metrics
if grep -q "fitness_passed.*false" mining_reports/*.json; then
  echo "Mining quality gate failed"
  exit 1
fi
```

### 4. Push to Documentation System

```bash
# Ingest to DocHub or similar
discover ingest -p todoapp --target dochub
```

---

## Key Metrics

After discovery, you'll see metrics like:

| Metric | Example |
|--------|---------|
| Code Nodes Parsed | 234 |
| Domains Identified | 5 |
| Scenarios Discovered | 12 |
| Process Flows | 8 |
| Documents Generated | 45 |
| Domain Edges | 18 |
| Fitness (mining) | 94.0% |
| Avg Cycle Time | 2.3s |

---

## Support

For issues or questions:

1. Check logs: `cat data/todoapp/discovery.log | grep -i error`
2. See documentation: `docs/PM4PY_INTEGRATION.md`
3. Run examples: `python examples/process_mining_example.py`
4. Check GitHub: [davidfowl/TodoApp](https://github.com/davidfowl/TodoApp)

---

## License

This demo script is part of AI-Discovery.  
The TodoApp repository is licensed under the MIT License (see repository for details).

---

**Ready?** Run: `./demo.sh`

```
╔════════════════════════════════════════════════════════════════╗
║         AI-Discovery Demo: Discover Real Applications          ║
╚════════════════════════════════════════════════════════════════╝
```
