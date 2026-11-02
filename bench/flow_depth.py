"""Flow-tracer bounds measurement (roadmap M6.17): slices at depth 5 (before) vs depth 8, uncapped and with the
50-flow cap, on scans of the pinned bench repos. Prints JSON (docs/roadmap/benchmarks/raw/m617-flow-depth.json).

    python -m bench.flow_depth <dir>     # <dir>/scan-<id>.json = `discover --json scan --full --no-prose` output
                                         # for ids microblog, node, petclinic, eshop

Tier-2 input tokens are the three scenario prompts (steps, IPO, interfaces) at 4 chars/token, plus 40 tokens of
Tier-1 summary per node and prompt (a keyless scan has no summaries).
"""
import json, sqlite3, statistics, sys
from collections import Counter
from pathlib import Path
from ai_discovery.pages.registry import BuildContext
from ai_discovery.pages.builders.flows import FlowGraph
from ai_discovery.ai.flow_analyzer import ScenarioFlowInference

S = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
SUMMARY_TOKENS = 40          # purpose + io lines per node in a prose scan (no summaries in a keyless scan)
inf = ScenarioFlowInference(None)

def tokens(sc):
    nodes = len(sc.nodes)
    steps = len(inf._build_steps_prompt(sc, {})) / 4
    ipo = len(inf._build_ipo_prompt(sc, [], {})) / 4
    ifc = len(inf._build_interfaces_prompt(sc, [], {})) / 4
    return steps + ipo + ifc + 3 * SUMMARY_TOKENS * nodes

out = {}
for rid in ("microblog", "node", "petclinic", "eshop"):
    scan = json.loads((S / f"scan-{rid}.json").read_text().strip().splitlines()[-1])
    conn = sqlite3.connect(scan["db"])
    g = FlowGraph(BuildContext(conn=conn, scan_id=scan["scan_id"], repo=Path(scan.get("repo") or ".")))
    row = {"entries": len(g.entries)}
    for label, depth, flows in (("before d5", 5, 10**9), ("d8 uncapped", 8, 10**9), ("after d8+cap50", 8, 50)):
        scs = [g.slicer.build_scenario(g.code[ep.node.qualified_name], max_depth=depth, max_flows=flows)
               for ep in g.entries]
        n = [len(s.nodes) for s in scs]
        stops = Counter(r for s in scs for r in s.stop_reasons.values())
        row[label] = {"nodes_total": sum(n), "nodes_median": statistics.median(n) if n else 0,
                      "nodes_max": max(n, default=0),
                      "flows_max": max((len(s.stop_reasons) for s in scs), default=0),
                      "scenarios_depth_capped": sum(1 for s in scs if "depth_cap" in s.stop_reasons.values()),
                      "scenarios_flow_capped": sum(1 for s in scs if "flow_cap" in s.stop_reasons.values()),
                      "tier2_input_tokens": round(sum(tokens(s) for s in scs)),
                      "stops": dict(stops)}
    out[rid] = row
print(json.dumps(out, indent=1))
