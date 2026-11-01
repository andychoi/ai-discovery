# ADR-0007: Process views are evidence-backed; no BPMN from static analysis

- **Status:** Accepted
- **Date:** 2026-11-01
- **Related items:** M4.4.b, M3.11, M8.3, M8.6; supersedes the BPMN parts of `docs/architecture/decisions.md`
  ("MANUAL Node Injection Heuristics") and of `design/wiki-web.md` (`/flows/:id` BPMN view)

## Context
M4.4.b originally asked the wiki to render **BPMN** for flow pages and **DMN** for entity pages, using the
vendored bpmn-js/dmn-js viewers. Before building that, we checked what the BPMN would be made of.

**What BPMN asserts.** A BPMN diagram is a process model. It states:
- control flow: sequence, exclusive (XOR) and parallel (AND) gateways, loops;
- events: start, end, timer, message;
- wait states, and the *actors* (lanes) who do each task;
- a process boundary that spans many requests, sessions, days and people.

**What static analysis observes.** A call graph is a *may-call* relation inside one request or job. It does not
see branch semantics (whether two calls happen in sequence, either-or, or both), ordering across requests,
waits, timers that fire outside the code, or people. In a brownfield system the steps of a business process are
stitched together by **persisted state** (`order.status = SUBMITTED`), queues, schedules and humans. None of
these appears as a call edge.

**What the current BPMN is made of** (`generators/bpmn_generator.py`, phase 15):
- **Gateways.** Any function that calls more than one other function becomes an exclusive gateway
  (`graph/call_graph.py::_detect_alternate_paths`), and branch conditions are guessed from callee names
  (`success`, `error`, `branch_2`, …). Calls made one after another are drawn as either-or choices.
- **Steps.** These come from `ScenarioFlowInference`, which is an LLM reconstruction. The LLM invents `USER_TASK`
  approval/review steps, and we already mark those `⚠ inferred` (HIGH-9).
- **Lanes.** These come from step types (`DB` → "External"), not from roles.

Rendered in a BPMN viewer, the result *looks* like a specification that a business analyst could sign off,
but it is mostly inference. That is the same failure mode as the pm4py integration we reversed in June 2026
(fitness hard-coded to 1.0 and synthetic timestamps, `decisions.md`): an authoritative-looking artifact with no
evidence behind it.

DMN has a different status. Entity decision tables are built only from **parsed guard expressions** (and
correlations mined from scenarios), and transitions with no guard are left out. They are grounded, but a decision
table is still a table, and dmn-js adds a 1 MB viewer to show what a markdown table already shows.

## Decision
1. **The process backbone is the entity lifecycle.** This is artifact-centric process modeling: a business
   process in a brownfield system is read as the lifecycle of its business objects. Each lifecycle arrow is a
   literal state assignment in code (confidence 1.0). The wiki shows **what drives each arrow**: the trigger
   function, its guard, the flows (endpoints, jobs) whose resolved call slice reaches the trigger, and the
   screens that start those flows. Every link in that chain is a stored, cited fact.
2. **Flow pages stay per-trigger traces.** They are a call slice from one entry point along resolved edges, with
   where the trace stops and what data it touches. They are labelled as traces. They are not drawn as process
   diagrams, and fan-out is never drawn as a gateway.
3. **Drilldown, not a diagram, connects the levels:** screen → flow → entity (state change) → table. Pages link
   in that direction (`routes_to`, `writes`, `see_also`), and the wiki groups links and backlinks by those
   levels.
4. **DMN is shown as markdown decision tables, from parsed guards only.** No dmn-js.
5. **The wiki ships no BPMN viewer.** bpmn-js and dmn-js are removed from the web bundle.
6. **Generating BPMN from scenario flows is retired** (M3.11). That covers the phase-15 BPMN XML, the fan-out
   gateway heuristic, and LLM-invented `USER_TASK` steps. MCP `get_flow` stops advertising `bpmn_available`.
7. **When a process model may come back.** It may return only from **observed** evidence: object-centric
   process discovery over real events (status-history or audit tables, OTLP spans from M8.3). It may also come
   from a model that a person confirms in the wiki (notes and annotations). It would then be exported (BPMN or
   an OCEL-based net) with each element labelled observed, confirmed or inferred. This is Exploring item M8.6;
   it is not v1.

## Each signal and how we treat it

| Signal | Static evidence? | Treatment |
|---|---|---|
| State change `entity.status = X` | yes (literal assignment, 1.0) | lifecycle arrow |
| Guard on that change | yes, when parsed | DMN row, guard column |
| Which endpoint, job or screen drives it | yes (resolved slice reaches the trigger; screen mapping) | "Driven by" table, drilldown links |
| Order of states across requests | partly (the lifecycle graph) | lifecycle diagram only, no sequence claimed |
| XOR / AND branching | no (a call graph cannot tell sequence from choice) | not drawn |
| Human / manual steps, approvals | no | not drawn; a reader may add a note |
| Waits, timers, durations, frequencies | no | stated as a limit on the page; M8 overlays |

## Consequences
- Business readers get fewer diagrams, and every diagram they do get is true. Pages state their limits
  ("who does a step, how long it waits, how often a path is taken: not visible from code").
- The web bundle loses bpmn-js and dmn-js. That shrinks the bundle and the dependency surface.
- Retiring phase-15 BPMN (M3.11) crosses lanes A, B, C and E. It is a separate item with its own golden and MCP
  changes. It landed after M4.4.b: `scenario_flows.bpmn_xml` is no longer written (the column stays for old
  stores), and stored gateway and manual steps are rewritten when they are read (`ai/flow_steps.py`).
- A future process model (M8.6) must carry an evidence label on every element. This ADR sets that as the bar.
