> **Review note (2026-10-08):** several claims below were checked and found wrong or overstated. Examples: repowise is not a file concatenator (that is repomix); eBPF and OTel auto-instrumentation give request-level spans, not method chains; the .NET profiler GUID below is wrong (the correct one is `{918728DD-259F-4A6A-AC2B-B85E1B658318}`); traceAI and opentelemetry-skill are misdescribed. See `docs/roadmap/research/research-doc-review.md` before relying on this document. Adopted ideas are mapped to roadmap items M5 and M8.

Unlike RAG-based AI tools that look at text snippets out of order, [Claude Code navigates a codebase exactly like an engineer](https://claude.com/blog/how-claude-code-works-in-large-codebases-best-practices-and-where-to-start). It doesn't rely on stale database indexes; instead, it performs agentic search, dynamically running `grep`, reading files, tracing execution loops, and using local language servers to follow active code references in real time. [1, 2]

When dealing with a messy brownfield codebase, you can leverage its native behaviors to systematically map out business logic, calculate the ripple effects of a change, and execute edits without breaking hidden dependencies.

---

## Phase 1: Context Discovery (The "Read & Map" Phase)

Before letting Claude edit anything, use its agentic exploration to build a reliable mental model of the existing data structures and workflows. [3]

- Map the Data Flow (Entry to Storage):  
    Don't just ask where a file is. Force Claude to trace the actual lineage of data from your endpoints down to your database.
    
    - _Prompt:_ `"Trace the entire execution flow of a user updating their profile. Use grep to find the entry point API controller, read the validation logic, and show me exactly which repository or DB layer file handles the final persistence."*
    
- Locate Implicit Business Rules:  
    In brownfield code, domain logic is often buried inside random helper modules or controllers. Ask Claude to isolate these clusters.
    
    - _Prompt:_ `"Locate where the core business logic for calculating [e.g., subscription tier limits or processing fees] is defined. Scan for any calculations, conditionals, or hardcoded constants matching these rules across the system."*
    

## Phase 2: Impact Analysis (The "Blast Radius" Check)

Claude excels at acting as an adversarial reviewer. Once you know _where_ a change needs to happen, make Claude calculate the exact blast radius of that modification across the entire architecture. [2]

- Expose Hidden Dependencies:  
    Ask Claude to act as a dependency graph parser.
    
    - _Prompt:_ `"I need to modify the data structure of the [e.g., Kanban card entity] by changing field X to Y. Run grep and follow references to find every module, service, validation schema, or UI component that reads from or writes to this specific data structure. Flag any potential breaking points."*
    
- Evaluate Side Effects on Shared State:
    
    - _Prompt:_ `"If I alter the initialization logic inside [file.js], what downstream modules will experience side effects? Follow the lifecycle imports to ensure we don't break singleton instances or shared context state."*
    

## Phase 3: Planning the "Right Way" of Changes

Brownfield applications have unwritten stylistic and architectural rules. Instead of letting Claude generate generic code, force it to adapt to your code's unique idiom.

- Extract Code Design Patterns:  
    Instruct Claude Code to read your best-written modules to establish a structural standard.
    
    - _Prompt:_ `"Analyze [GoodExampleService.java] and [GoodExampleController.java]. Identify the design tokens, error handling mechanisms, and logging patterns used there. Write out a step-by-step implementation plan for modifying [TargetService.java] that strictly mimics this exact architectural style."*
    
- Execute under Constraint Safely:  
    Break execution into a plan-and-review cycle rather than a blind rewrite.
    
    - _Prompt:_ `"Draft the proposed changes to the logic in markdown pseudocode first. Do not modify the actual source files yet. Highlight how you plan to preserve backward compatibility for existing consumers of this API."*
    

---

## Pro-Tips for Brownfield Mastery

|Native Behavior|How to Optimize for Brownfield Code|
|---|---|
|No Index Syncing|Because Claude reads the current disk state dynamically, you can confidently branch, modify, or stash code. Claude will immediately read your local changes without needing a re-indexing phase.|
|`CLAUDE.md` Layer|Keep a `CLAUDE.md` file in your root folder. Add a subsection explicitly dedicated to "Gotchas & Legacy Constraints" (e.g., _"Do not use npm packages outside package.json, we rely heavily on native vanilla DOM interfaces for performance"_). Claude reads this layer constantly.|
|Language Server Sync|Maximize [Claude Code's native Code Intelligence capabilities](https://code.claude.com/docs/en/features-overview). Ensure your local project workspace is fully initialized (e.g., `npm install` completed, types generated) so Claude can leverage underlying language server diagnostics to trace definitions seamlessly.|


[1] [https://claude.com](https://claude.com/blog/how-claude-code-works-in-large-codebases-best-practices-and-where-to-start)

[2] [https://ranjankumar.in](https://ranjankumar.in/claude-code-brownfield-harness-engineering)

[3] [https://www.linkedin.com](https://www.linkedin.com/posts/danielwalshireland_claude-code-is-incredible-at-finding-exactly-activity-7426948386525839361-LPXC)

---
사람이 매번 복잡한 프롬프트를 기억하고 일일이 컨텍스트를 추적하는 것은 불가능에 가깝고, 결국 ‘감(Vibe)’에 의존한 코딩으로 이어져 브라운필드 코딩을 망치게 됩니다.

그렇기 때문에 이 과정을 Business Analyst(BA) AI Agent와 Software Engineer(SWE) AI Agent의 협업 구조(Multi-Agent System)로 자동화하는 파이프라인을 구축해야 합니다.

실제로 사람이 개입하지 않고 요구사항 입력만으로 Phase 1, 2, 3를 스스로 계획하고 실행하는 워크플로우는 다음과 같이 설계할 수 있습니다.
1. Phase 1: Context Discovery (The "Read & Map" Phase)
2. Phase 2: Impact Analysis (The "Blast Radius" Check)
3. Phase 3: Planning the "Right Way" of Changes
---

## 🤖 BA Agent 중심의 3단계 자동화 아키텍처

사람이 "보드의 카드 정렬 규칙을 생성일 순에서 우선순위 순으로 바꿔줘"라고 한 줄만 입력하면, 내부에서 BA Agent가 중심이 되어 다음과 같이 작업을 쪼개고 실행합니다.

## 📋 Step 1: 계획 및 탐색 위임 (BA Agent ➔ 도구/SWE Agent)

BA Agent가 비즈니스 요구사항을 기술적 탐색 목표로 변환합니다.

- BA의 판단: "정렬 규칙 변경은 데이터 모델(DB), API 엔드포인트, 프론트엔드 UI 컴포넌트 모두에 영향을 준다."
- 실행 액션: BA Agent가 구체적인 검색 쿼리를 생성하여 코드 검색 도구(Grep 등)나 하위 SWE Agent에게 전달합니다.
    
    - _예시 명령:_ `find_references("card_order")`, `search_code("SortCriteria")`
    

## 🔍 Step 2: 영향도 및 설계 명세서 작성 (BA Agent의 핵심 역할)

하위 도구들이 수집해온 파일 목록과 코드 구조를 보고 BA Agent가 영향도 분석 보고서(Impact Analysis)와 구체적인 구현 스펙(Spec)을 스스로 작성합니다.

- BA의 분석: "이 변경은 `card_service.py` 42번째 줄의 쿼리와 프론트엔드 `kanban.js`의 드래그 앤 드롭 렌더링 로직을 깨뜨릴 위험이 있음."
- 산출물: 사람이 읽고 승인할 수 있는 `IMPLEMENTATION_PLAN.md`를 자동으로 생성합니다.

## 🛠️ Step 3: Claude Code 실행 및 검증 (BA Agent ➔ Claude Code CLI)

작성된 스펙을 바탕으로 BA Agent가 Claude Code(또는 코딩 에이전트)에게 명령을 내려 코드를 수정하게 합니다.

- 실행 액션: `claude "Read IMPLEMENTATION_PLAN.md and modify the codes accordingly."`
- 자동 검증: 수정이 끝나면 BA Agent가 스스로 터미널 명령을 내려 테스트 세션을 돌립니다 (`pytest`, `npm test` 등). 에러가 나면 Claude Code에게 로그를 먹여 스스로 고치게(Self-healing) 만듭니다.

---

## 🛠️ 실제로 이 시스템을 어떻게 구축하나요?

현재 개발자 커뮤니티나 오픈소스 생태계에서 이를 구현하기 위해 사용하는 대표적인 방법 3가지는 다음과 같습니다.

|방식|어떻게 작동하는가?|특징|
|---|---|---|
|Moai ADK / Paperclip 스타일|요구사항을 던지면 내부에서 Plan ➔ Spec ➔ Implement ➔ Run/Sync 루프를 스스로 돌리는 프레임워크를 연동합니다.|사람이 중간에 Spec만 승인하면 전체 파이프라인이 자동화됩니다.|
|LangGraph / CrewAI 기반 구축|BA 역할을 하는 LLM Agent와 Claude Code CLI를 실행하는 SWE 역할의 Agent를 그래프(Graph) 형태로 묶어 서로 대화하며 코딩하게 만듭니다.|우리 팀의 브라운필드 규칙(Legacy 규칙 등)을 BA Agent에게 완벽히 주입할 수 있습니다.|
|Custom MCP (Model Context Protocol) 툴|Claude Code에 "비즈니스 로직 맵 생성기"나 "영향도 분석기" 역할을 하는 자작 MCP 툴을 붙여, Claude가 스스로 이 단계를 밟도록 유도합니다.|Claude Code를 그대로 쓰면서도 Vibe 코딩을 방지하는 가장 가벼운 방법입니다.|

## 💡 사람이 해야 할 유일한 일: `CLAUDE.md` 가이드라인 제공

아무리 똑똑한 BA Agent도 우리 회사의 히스토리나 "절대 건드리면 안 되는 레거시 코드"는 알 수 없습니다. 따라서 루트 디렉토리에 `CLAUDE.md`나 에이전트용 프롬프트 저장소에 아래와 같은 명확한 제약사항만 적어두면 됩니다.

```markdown
# 프로젝트 규칙 (Agent 지침용)
- 이 프로젝트는 빌드 스텝이 없는 순수 Vanilla JS 환경입니다. 절대 `npm install`이나 패키지 추가를 하지 마세요.
- 모든 비즈니스 규칙 변경은 구현 전에 반드시 `IMPACT_REPORT.md`를 생성하여 인간의 승인을 받아야 합니다.
- 수정 후에는 반드시 린터(`eslint`)와 유닛 테스트를 통과해야 커밋할 수 있습니다.
```

이렇게 시스템을 한 번 구축해 두면, 인간은 '바이브 코딩 프롬프트 짜기'에서 해방되어 비즈니스 요구사항 전달과 최종 코드 승인만 담당하는 진짜 '관리자'이자 '아키텍트'가 될 수 있습니다.

---

My benchmark findings point directly to the central problem of modern LLM context retrieval: repacking structural code into raw context tokens (`repowise`) or forcing text-similarity searches (`generic RAG MCPs`) does not increase, and can even hurt, deep logical understanding.

As you noted, when native tool exploration is fully available, advanced models frequently bypass non-native MCP wrappers altogether because sequential token scanning cannot mimic active code compilation.

Given these precise empirical directions, here is the realistic assessment of whether you can completely trust Claude Code for context discovery and blast radius identification—and how to fix its blind spots.

---

## 1. Can you completely delegate Context Discovery to Claude Code?

No, you cannot safely delegate it 100% blindly, but you can delegate the execution if you enforce the right structural guardrails.

Your benchmark highlights a critical architectural split in model behavior:

- Sonnet’s "Early Stop" Bias: Sonnet naturally prioritizes precision and stops exploring early (20–30s) to keep things clean. In a complex, layered brownfield codebase, this exact behavior leads it to overlook hidden reflections, indirect database triggers, or downstream dynamic actions.
- GLM's "High Recall" Advantage: A thorough model explores 2–3× more aggressively to achieve high logical coverage. Sonnet requires explicit manual constraint to match that level of thoroughness.

## Why Repowise-style tools fail here

Tools like `repowise` simply concatenate files or flatten directories. Forcing Claude to parse massive flattened files blows out its inner-monologue logic, turning a precise search task into a broad text-matching task. This dampens the model’s focus, resulting in lower recall.

---

## 2. Can Claude Code accurately extract the Blast Radius?

It can extract explicit imports and static dependencies with near-perfect accuracy, but it will miss dynamic, implicit, and un-typed relationships unless forced to use specialized tools.

If your codebase relies on:

1. Dynamic reflection or dependency injection strings (e.g., Spring XML strings or decoupled Node modules loaded at runtime)
2. Decoupled event-driven patterns (e.g., Pub/Sub, event emitters, or global message queues)
3. Loose database schemas without foreign key constraints defined in the code code layer

Then Claude's native `grep` and static lookups will fundamentally miss portions of the blast radius.

---

## 3. The Countermeasure: Transition from "Text-RAG" to "Graph/Code Intelligence"

To elevate your code integrity without human-in-the-loop "vibe prompting," your BA Agent pipeline needs to transition away from standard file-dump MCPs and instead configure Claude Code to use Code Intelligence tools.

Instead of `repowise`, your BA Agent should control Claude through these structural methods:

## A. Force Sonnet to Overcome its "Early Stop" Bias

Since Sonnet naturally stops early, your BA Agent must explicitly program the depth of the exploration loop via multi-turn instruction:

- Bad: _"Find the blast radius of changing variable X."_
- Good: _"Perform a multi-tiered dependency trace. Tier 1: Identify all files directly importing target X. Tier 2: For each file discovered in Tier 1, run a recursive search to identify its immediate consumers. Do not conclude your analysis until you have traced down to the final entry point controller/route."_

## B. Replace Text Search with a Code Graph RAG

Instead of generic text embeddings, use tools that parse codebases into an AST (Abstract Syntax Tree) Graph.

- If you feed Claude tools that map graph connections (e.g., an MCP server wrapped around an active Language Server Protocol (`LSP`) or tools like `code-graph-rag`), Claude doesn't have to guess via text matching. It can immediately read actual semantic definitions, parent/child class implementations, and precise type intersections.

## C. Synthesize a Validation Harness (The Absolute Baseline)

Because an AI will always risk missing a logical path, code awareness must be verified programmatically:

```unset
[BA Agent Spec Draft] ──► [Claude Code Native Implementation]
                                   │
                                   ▼
[Failure Logs Fed Back] ◄── [Strict Type/Linter Compiler Check]
```

If the static compilation layer (`tsc`, `mvn compile`, `cargo check`) fails, your BA Agent intercepts the output, blocks any PR generation, and passes the strict compiler failure back to Claude for immediate adjustment.

## Summary

Keep letting Claude Code use its native search capabilities—they beat generic RAG extensions because they act agentically. However, to guarantee an accurate blast radius evaluation in brownfield environments, your orchestration layer must explicitly enforce multi-tiered recursive analysis routines and rely heavily on strict compiler/test loops to act as the ultimate truth machine.

---

## 참조 REPO

단순한 텍스트 RAG(`repowise` 등)의 한계를 느끼고, AST(구문 트리) 분석과 지식 그래프(Knowledge Graph)를 결합하여 AI에게 코드의 '공간 감각'과 '진짜 Blast Radius(영향도)'를 제공하는 획기적인 오픈소스 프로젝트들이 생태계에 등장해 있습니다. [1]

BA Agent 파이프라인이나 Claude Code에 연동하여 Vibe 코딩을 방지할 수 있는 대표적인 레포지토리들을 성격별로 분류해 드립니다.

---

## 1. 전역 구조 및 Blast Radius 추적 전용 (Graph RAG 계열)

코드베이스를 Neo4j나 Memgraph 같은 그래프 DB로 변환한 뒤, AI가 Cypher 쿼리를 날려 함수 호출 체인과 의존성을 추적하게 만드는 프로젝트입니다. [2, 3]

- [vitali87 / code-graph-rag](https://github.com/vitali87/code-graph-rag)
    
    - 특징: Tree-sitter를 기반으로 작동하며, 컴파일러 프론트엔드Fact를 결합하여 monorepo 전체를 하나의 지식 그래프로 빌드합니다.
    - 강점: 자연어로 질문하면 내부적으로 Cypher 그래프 쿼리로 변환하여 정확한 dead code 탐색, AST 기반의 코드 패칭, 런타임 추적 데이터 결합(eBPF/Test trace)까지 지원하므로 dynamic 언어의 암시적 영향도 파악에 매우 강력합니다. [3, 4]
    
- [Abhishek-Aditya-bs / CodeGraph](https://github.com/Abhishek-Aditya-bs/CodeGraph)
    
    - 특징: 코드베이스를 Neo4j 지식 그래프로 변환하고, 그 위에 하이브리드 Vector Embedding 구조를 결합한 프로젝트입니다.
    - 강점: "이 모듈을 바꾸면 어디가 깨져?" 같은 질문을 받았을 때, 텍스트 유사도 매칭이 아닌 실제 Call Graph 토폴로지(관계선)를 타고 들어가 주변 구조 컨텍스트를 통째로 수집해 LLM에게 전달합니다. 토큰 소모를 크게 줄이면서 정확도를 높입니다. [2, 5]
    

---

## 2. Claude Code / Cursor 직접 연동형 (AST MCP Server 계열)

Claude Code나 Cursor가 API처럼 호출하여 실시간으로 정확한 코드 구조(Type, Declaration)를 가져갈 수 있도록 설계된 전용 MCP 서버들입니다. [6, 7]

- [DeusData / codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
    
    - 특징: 고성능 C 기반의 코드 인텔리전스 엔진으로, 레포지토리를 SQLite 지식 그래프로 인덱싱하는 초경량 static binary MCP 서버입니다. 리눅스 커널 전체를 3분 만에 인덱싱할 만큼 빠릅니다.
    - 제공 도구: `get_architecture`, `trace_path`, `detect_changes`, `search_graph` 등 17개의 전용 MCP 도구를 Claude에게 쥐어줍니다. [8]
    
- [ast-grep / ast-grep-mcp](https://github.com/ast-grep/ast-grep-mcp)
    
    - 특징: 유명한 구조적 코드 검색 도구인 `ast-grep`을 AI 에이전트용 MCP로 래핑한 공식 실험적 서버입니다.
    - 강점: 정규식(Regex) 기반의 텍스트 grep이 아니라, "특정 데코레이터가 붙은 클래스 중 X 인터페이스를 상속받은 타겟을 찾아라" 같은 AST 패턴 매칭을 Claude가 직접 실행할 수 있게 만듭니다. [6]
    
- [yailPeralta / ast-mcp-server](https://github.com/yailPeralta/ast-mcp-server) (TS/JS 진영인 경우)
    
    - 특징: 단순 텍스트 추측을 배제하고, 실제 TypeScript 컴파일러 프로젝트 모델(`ts-morph`)을 사용해 정밀한 AST 정보를 제공합니다.
    - 강점: AI에게 변수의 레퍼런스 위치, 타입 진단(Diagnostics) 결과 등을 컴파일러 레벨의 정확도로 바인딩합니다. [7]
    

---

## 💡 BA Agent 아키텍처에 적용하는 시나리오 추천

벤치마크 결과에서 "Sonnet은 탐색을 너무 일찍 멈춘다(Early Stop Bias)"는 약점이 명확해졌으므로, 이 에이전트들을 다음과 같이 믹스앤매치(Mix & Match)해야 정답률이 올라갑니다.

1. BA Agent의 1단계 (Graph Query 위임): 사람이 요구사항을 던지면, BA Agent가 `code-graph-rag` 또는 `CodeGraph` CLI/API를 통해 수정 대목의 Call Chain(호출 노드 목록)을 텍스트가 아닌 구조적 JSON 리스트로 먼저 뽑아냅니다. [2, 3]
2. BA Agent의 2단계 (명세서 구체화): 뽑혀 나온 구조적 리스트를 기반으로 BA Agent가 조기 종료하지 못하도록 타겟 파일 목록을 강제로 지정하여 `IMPLEMENTATION_PLAN.md`를 작성합니다.
3. Claude Code의 3단계 (AST 검증 수정): Claude Code가 실제 작업에 착수할 때 `ast-grep-mcp`나 `codebase-memory-mcp`를 활성화해 둠으로써, 수정 과정에서 린터나 타입이 깨지는지 real-time으로 체크하며 수정하게 제어합니다. [6, 8]

혹시 현재 다루고 계신 메인 프로젝트의 주요 언어 스택(TypeScript, Python, Java, C++ 등)이 무엇인가요? 언어에 따라 `ts-morph` 기반을 쓸지, `Clang/Clangd` 계열을 얹을지 가장 유효한 오픈소스 조합을 더 좁혀드릴 수 있습니다. [7, 9]

  

[1] [https://github.com](https://github.com/angrysky56/ast-mcp-server)

[2] [https://github.com](https://github.com/Abhishek-Aditya-bs/CodeGraph)

[3] [https://skillsllm.com](https://skillsllm.com/skill/code-graph-rag)

[4] [https://github.com](https://github.com/vitali87/code-graph-rag)

[5] [https://www.reddit.com](https://www.reddit.com/r/mcp/comments/1rjdoag/built_an_mcp_server_that_gives_ai_agents_a_full/)

[6] [https://github.com](https://github.com/ast-grep/ast-grep-mcp)

[7] [https://github.com](https://github.com/yailPeralta/ast-mcp-server)

[8] [https://github.com](https://github.com/DeusData/codebase-memory-mcp)

[9] [https://github.com](https://github.com/2015xli/clangd-graph-rag)

---


## 1. AST는 여전히 Static(정적) 분석 기반 아닌가?

네, 100% 정적 분석이 맞습니다.

AST(구문 트리)는 컴파일러가 코드를 빌드하기 직전에 텍스트를 구조화한 트리일 뿐이므로, 다음과 같은 브라운필드의 런타임(Runtime) 동적 특성을 잡지 못합니다.

- 의존성 주입(DI): 인터페이스 주입, Spring의 XML/어노테이션 기반 빈(Bean) 스캐닝, Node.js의 dynamic import나 리플렉션.
- 이벤트 기반 아키텍처: Pub/Sub 메시지 큐, Event Emitter 등 코드가 느슨하게 결합(Decoupled)되어 있어 정적으로는 선이 연결되지 않는 경우.

## 💡 해결책: Static AST + Runtime Trace의 결합

그래서 앞서 언급한 고도화된 코드 인텔리전스 툴(예: `code-graph-rag` 등)은 정적 AST에 런타임 데이터(OpenTelemetry, eBPF 로그, 또는 테스트 코드 실행 트레이스)를 데이터베이스 상에서 결합(Hydration)하는 방식을 취합니다.  
에이전트가 "테스트 코드가 돌 때 이 함수를 거쳐 가는 실제 런타임 콜 스택"을 정적 그래프 위에 얹어서 보게 만들어야 이 한계를 극복할 수 있습니다.

---

## 2. Repowise도 Graph RAG를 지원하지 않는가?

최근 `repowise`나 유사 툴들이 내부적으로 파일 간의 의존성 그래프나 토큰 간의 간이 관계를 임베딩하여 Graph RAG 형태의 기능을 제공하기 시작했습니다. 하지만 본질적인 한계가 있습니다.

- 텍스트 기반의 가짜 그래프: `repowise` 계열의 그래프는 엄밀한 의미의 '코드 시맨틱 그래프(Semantic Graph)'라기보다, 파일 간의 import 관계나 텍스트 청크(Chunk) 간의 유사도 연결에 가깝습니다.
- 컨텍스트 유효성 붕괴: 이를 Claude Code 같은 모델에게 텍스트 정보로 변환해 전달하면, Sonnet은 방대한 관계 텍스트를 읽다가 앞서 벤치마크 결과처럼 컨텍스트 피로도로 인해 탐색을 조기에 종료(`Early Stop Bias`)해 버립니다.
- 결론: 툴 자체의 지원 여부보다 "LLM에게 날것의 텍스트 형태로 컨텍스트를 주입하느냐", 아니면 "에이전트가 데이터베이스에 정밀한 쿼리를 날려 필요한 노드만 낚아채 오느냐"의 아키텍처적 차이가 정확도를 가릅니다.

---

## 3. 호출 깊이가 깊거나 순환 구조면 대상 파일이 폭발하지 않는가?

가장 현실적인 문제입니다. 조금만 규모가 큰 레거시(Brownfield) 모노레포에 들어가면, 공통 유틸리티나 핵심 데이터 모델 하나를 건드렸을 때 하위 전파 경로를 다 뽑으면 수백 개의 파일이 매핑되어 컨텍스트 윈도우가 폭발하거나 비용이 감당되지 않습니다.

이를 막기 위해 BA Agent 파이프라인에는 반드시 수학적/구조적 필터링 장치(Pruning)가 들어가야 합니다.

## 🛠️ 파일 폭발을 막는 에이전트 제어 전략 3가지

```unset
[원인 노드] ──► [중간 도메인 서비스] ──► [공통 미들웨어 (차단)] ──► [수백 개의 UI 컴포넌트]
                          ▲
                  (여기까지만 탐색 범위 제한)
```

1. 중단점 레이어(Boundary Definers) 지정:
    
    - 그래프를 탐색할 때 최상단 프론트엔드 UI 컴포넌트나 최하단 인프라스트럭처 레이어까지 다 치고 올라가지 않도록, "핵심 도메인 서비스(Domain Services) 레이어"까지만 추적하도록 깊이 제한(Max Depth = 2 or 3)을 쿼리에 명시해야 합니다.
    
2. 순환 참조 디텍션 (Cycle Detection):
    
    - 그래프 데이터베이스(Neo4j 등) 상에서 `DFS/BFS` 알고리즘이나 `PageRank` 같은 알고리즘을 사용해 이미 방문한 노드는 에이전트의 탐색 경로에서 강제로 제외하는 순환 참조 방지 로직이 툴 레벨에서 작동해야 합니다.
    
3. 영향도 가중치(Impact Scoring) 도입:
    
    - 모든 연결된 파일이 다 중요한 것은 아닙니다. 수정할 코드가 미치는 영향도를 "단순 타입 참조(낮음)", "메서드 직접 호출(높음)", "데이터 쓰기 작업(매우 높음)"으로 분류하여, 가중치가 높은 핵심 핵심 경로(Critical Path)의 파일 톱 5~10개만 슬라이싱해서 에이전트에게 쥐어주어야 합니다.
    

---

##  결론

결국 사람이 프롬프트를 아무리 잘 짜도(Vibe Coding), 코드의 복잡도가 임계점을 넘으면 AI는 길을 잃습니다.

유효성과 정확성을 확보하는 유일한 현실적 방법은 "AI가 코드를 다 읽고 판단하게 만들지 않는 것"입니다. 대신, 외부에서 Graph DB와 컴파일러가 계산한 정밀한 영향도 요약본(JSON)을 먼저 만든 뒤, BA Agent가 이를 딱 5개 이내의 '핵심 타겟 파일 목록'으로 가공하여 Claude Code에게 수정 타겟을 강제로 지정(Force Pinning)해 주는 아키텍처를 도입해야 승률이 올라갑니다.

만약 이 파이프라인을 구상하신다면, 현재 겪고 계신 브라운필드 코드베이스에서 가장 추적하기 까다로운 암시적 결합 패턴(예: dynamic event pub/sub, string-based DI, untyped legacy API 등)은 주로 어떤 형태인지 파악해야하고, 이에 맞춘 구체적인 런타임/정적 하이브리드 필터링 아이디어가 필요할 수 있음.

---

안 그래도 복잡한 레거시(Brownfield) 어플리케이션에 코드 인텔리전스를 하겠다고 소스 코드를 대대적으로 수정해야 한다면, 배보다 배꼽이 더 커지는 격이며 그 자체로 엄청난 리스크입니다.

다행히도, 최근의 관측 가능성(Observability) 기술은 레거시 소스 코드를 단 한 줄도 고치지 않거나(Zero-Code Modification), 인프라 수준에서 주입하는 방식을 지원합니다.

너무 복잡한 변경 없이 실무에서 가장 부하가 적게 이 세 가지를 에이전트 파이프라인에 활성화(Enabling)하는 현실적인 아키텍처 전략을 정리해 드립니다.

---

## 1. eBPF Log (가장 안전함: 코드 수정 0%)

eBPF(Extended Berkeley Packet Filter) 기술의 최대 장점은 어플리케이션 소스 코드나 런타임(JVM, Node 등)을 절대 건드리지 않는다는 점입니다. 리눅스 커널 레벨에서 프로세스의 시스템 콜(System Call)을 낚아챕니다.

- 어떻게 도입하나:  
    레거시가 도커 컨테이너나 리눅스 서버에서 돌고 있다면, 그 호스트 서버에 Grafana Beyla 또는 Pixie 같은 오픈소스 eBPF 에이전트를 데몬(Daemon)으로 띄우기만 하면 됩니다.
- 어떤 데이터가 뽑히나:
    
    - 어떤 파일/네트워크 엔드포인트를 통해 요청이 들어왔는지
    - 그 요청이 내부에서 어떤 함수/클래스를 거쳐 최종적으로 어떤 SQL 쿼리(DB 호출)로 이어졌는지의 런타임 의존성 맵을 소스 코드 변경 없이 통째로 추출할 수 있습니다.
    

## 2. OpenTelemetry (자동 주입: 코드 수정 0%)

OpenTelemetry(OTel)는 수동으로 개발자가 코드에 `tracer.startSpan()`을 심을 수도 있지만, 레거시 환경에서는 Auto-Instrumentation(자동 계측) 툴을 사용합니다. 언어별 런타임이 실행될 때 에이전트가 코드를 동적으로 가로채서(Bytecode Manipulation 또는 Wrapper) 로그를 심어줍니다.

- 언어별 도입 방식 (소스 코드 수정 없음):
    
    - Java: 애플리케이션을 실행할 때 VM 옵션만 한 줄 추가합니다.  
        `java -javaagent:opentelemetry-javaagent.jar -jar legacy-app.jar`
    - Node.js / Python: 메인 코드를 수정하는 대신, 패키지를 실행할 때 OTel 환경변수와 Preload 스크립트를 주입합니다.  
        `NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register" node app.js`
    
- 어떤 데이터가 뽑히나:
    
    - 레거시의 복잡한 추적 흐름(Trace Id)이 생성되며, "A 함수가 B 모듈을 거쳐 C API를 때렸다"는 실제 실행 스택 트리가 JSON 형태로 저장됩니다.
    

## 3. Test Code Trace (가장 현실적인 대안: 테스트 타겟팅)

만약 프로덕션이나 스테이징 서버에 eBPF나 OTel을 심는 것조차 인프라 보안/규정상 부담스럽다면, CI/CD 파이프라인(또는 로컬 개발 환경)의 테스트 코드 실행 단계만 타겟팅하는 것이 가장 깔끔합니다.

- 어떻게 도입하나:  
    기존에 이미 존재하는 유닛 테스트나 통합 테스트(`jest`, `pytest`, `junit` 등)를 실행할 때만 위에 언급한 OpenTelemetry 에이션트를 얹어서 실행하는 것입니다.
- 왜 이 방식이 효과적인가:
    
    - 안전성: 운영 서버를 건드리지 않으므로 성능 저하나 장애 리스크가 0%입니다.
    - AI 학습용 데이터 가치: 테스트 코드가 실행되면서 레거시의 핵심 비즈니스 로직(Happy Path, Edge Case)을 훑고 지나가기 때문에, 이때 발생하는 Trace 로그만 수집해도 "어떤 비즈니스 조건일 때 어떤 파일들이 엮여서 도는지" 완벽한 런타임 맵이 완성됩니다.
    

---

## 🛠️ 하이브리드 코드 인텔리전스 워크플로우 (최종 요약)

최종적으로 시스템 변경을 최소화하면서 정확성을 극대화하는 파이프라인 아키텍처는 다음과 같습니다.

```unset
[로컬/CI 테스트 실행] + [OTel Auto-Agent] 
        │
        ▼ (런타임 실행 흐름 발생)
[Trace JSON 로그 추출] ➔ [code-graph-rag (정적 AST와 결합)]
        │
        ▼ (진짜 영향도를 반영한 토폴로지 맵 완성)
[BA Agent 분석] ➔ [Claude Code에게 콕 집어서 파일 수정 지시]
```

결론적으로 복잡한 변경이 아닙니다. 소스 코드는 단 한 줄도 수정하지 마세요. 실행 환경의 스크립트나 환경변수(VM Option)에 에이전트를 한 줄 주입하는 것만으로도, Claude Code의 조기 종료 분산(Early Stop Bias)을 막아줄 초정밀 런타임 의존성 지도를 확보할 수 있습니다.

---

ASP.NET 애플리케이션(Dynamic .NET Framework 및 .NET Core/5+) 역시 소스 코드 수정 없이(Zero-Code Change) 완벽하게 지원합니다.

특히 마이크로소프트 진영은 전통적으로 런타임 프로파일링(Profiling) 기술이 고도로 발달해 있어, 인프라 레벨에서 주입하는 방식이 매우 안정적이고 직관적입니다.

구형 ASP.NET (Full .NET Framework 4.x / IIS 기반)과 신형 ASP.NET Core (.NET 6/7/8/9) 두 환경 모두에서 소스 코드를 건드리지 않고 OpenTelemetry와 런타임 트레이스를 활성화하는 구체적인 아키텍처 전략은 다음과 같습니다.

---

## 1. 신형 ASP.NET Core 환경 (.NET 6+)

.NET Core 이상 버전은 오픈텔레메트리 연동이 매우 쉽습니다. 소스 코드를 단 한 줄도 고치지 않는 `.NET CLR Instrumentation` 메커니즘을 사용합니다.

- 어떻게 주입하나:  
    애플리케이션을 호스팅하는 서버나 Docker 컨테이너, 혹은 CI/CD 실행 시점에 아래와 같이 환경 변수(Environment Variables) 세트만 주입해 주면 런타임 내부의 인젝터가 자동으로 코드를 인터셉트하여 트레이스를 생성합니다.
- 환경 변수 설정 예시:
    
    ```bash
    # OpenTelemetry .NET Auto-Instrumentation 활성화
    CORECLR_ENABLE_PROFILING=1
    CORECLR_PROFILER={91A16E64-D37F-49E8-924E-A340397E06CC}
    CORECLR_PROFILER_PATH=/getSignals/OpenTelemetry.AutoInstrumentation.Native.so
    DOTNET_STARTUP_HOOKS=/getSignals/OpenTelemetry.AutoInstrumentation.StartupHook.dll
    
    # 트레이스 데이터를 보낼 컬렉터 주소
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
    OTEL_SERVICE_NAME=my-legacy-aspnet-app
    ```
    
- 결과: 소스 코드는 그대로 둔 채, HTTP 요청이 Controller 엔드포인트를 치고 들어와 Entity Framework(EF Core)를 거쳐 SQL Server(DB)로 내려가는 모든 내부 메서드 실행 체인 및 쿼리 흐름이 고스란히 JSON 트레이스로 축출됩니다.

## 2. 구형 레거시 ASP.NET 환경 (.NET Framework 4.x + IIS)

많은 브라운필드 엔터프라이즈 앱이 여전히 Windows 서버의 IIS(Internet Information Services) 위에서 .NET Framework 4.8 등으로 구동됩니다. 이 환경도 소스 코드를 바꿀 필요가 없습니다.

- 어떻게 주입하나:  
    윈도우 서버(IIS 호스트)에 OpenTelemetry .NET Auto-Instrumentation Windows MSI 패키지를 설치하거나, PowerShell 스크립트를 통해 IIS 전역 인프라 수준에서 프로파일러 환경변수를 주입합니다.
- PowerShell 주입 명령 예시:
    
    ```powershell
    # IIS 가상 디렉토리 또는 시스템 전역 런타임에 프로파일러 등록
    [System.Environment]::SetEnvironmentVariable("COR_ENABLE_PROFILING", "1", "Machine")
    [System.Environment]::SetEnvironmentVariable("COR_PROFILER", "{91A16E64-D37F-49E8-924E-A340397E06CC}", "Machine")
    # 이후 IIS Reset 수행
    iisreset
    ```
    
- 결과: 구형 `System.Web.Http` 파이프라인과 ADO.NET SQL 커넥션 구조를 프로파일러가 로딩 타임에 낚아채어, 레거시 비즈니스 컴포넌트 간의 결합 관계와 흐름을 강제로 시각화(Observability)해 줍니다.

---

## 3. ASP.NET 환경에서 eBPF 활용성 (Windows vs Linux)

- Linux 환경의 ASP.NET Core: 만약 `.NET Core` 앱을 리눅스(Docker / K8s / Ubuntu)에서 돌리고 있다면, 앞서 말씀드린 Grafana Beyla 같은 eBPF 도구를 그대로 쓸 수 있습니다. 소스 코드 수정 및 환경변수 오버헤드조차 없이 완벽한 런타임 토폴로지가 나옵니다.
- Windows 환경의 구형 ASP.NET: eBPF는 리눅스 커널 기술이므로 Windows/IIS 진영에서는 기본적으로 사용이 어렵습니다. (현재 Windows용 eBPF 오픈소스 프로젝트가 MS 주도로 개발 중이나 프로덕션 급은 아닙니다.) 따라서 Windows 레거시 앱이라면 2번에서 언급한 OTel Auto-Instrumentation(CLR Profiler 방식)이 가장 확실하고 리스크 없는 정답입니다.

---

## 💡 BA Agent의 ASP.NET 코드 인텔리전스 결합 시나리오

인프라 세팅으로 한 번 추출된 ASP.NET의 런타임 트레이스는 다음과 같이 AI 아키텍처에 매핑됩니다.

1. 동적 의존성 바인딩: ASP.NET 특유의 인터페이스 기반 DI(`IServiceCollection` 이나 Autofac 같은 제3자 컨테이너) 때문에 정적 분석 툴이 "이 인터페이스의 실제 구현체가 무엇인지" 헷갈려할 때, 런타임 트레이스 로그가 "실제로는 `SqlUserRepository` 클래스가 호출되었음"을 명확히 증명해 줍니다.
2. 데이터베이스 Blast Radius 파악: EF Core나 LINQ 쿼리가 컴파일 시점에는 단순한 C# 메서드로 보이지만, 런타임 로그는 변환된 실제 SQL 쿼리와 타겟 데이터베이스 테이블명을 찍어줍니다. BA Agent는 이를 기반으로 어떤 데이터 스키마가 오염되거나 영향을 받는지 정확하게 판단합니다.

결론적으로, .NET 진영의 레거시 애플리케이션 역시 코드를 전혀 수정하지 않고 시스템 인프라 설정(환경변수 혹은 MSI 설치)만으로 AI 에이전트를 위한 초정밀 지도(Context Discovery 데이터)를 완벽하게 공급해 줄 수 있습니다.

---

Using OpenTelemetry (OTel) auto-instrumentation logs to anchor a Code Intelligence graph is a powerful architecture. It addresses the "missing runtime logic" blind spot inherent to static AST tools by mapping exactly what files, routes, methods, and queries execute in a production or testing environment.

The Top 5 Open Source GitHub projects leading the convergence of OpenTelemetry, code intelligence, and agentic Graph RAG provide structural maps and execution flows directly to AI agents:

---

## 1. [vitali87 / code-graph-rag](https://github.com/vitali87/code-graph-rag)

- The Concept: This repository represents the exact state-of-the-art for what you are trying to build. It is a multi-language Graph RAG platform specifically engineered to stop relying on text chunks. [1]
- How it uses OTel/Traces: It parses codebases into a [Memgraph knowledge graph](https://memgraph.com/) using Tree-sitter, but crucially incorporates runtime execution traces. By marrying static syntax structure with live trace definitions, it allows an AI agent to execute precise Cypher graph queries to compute the exact runtime blast radius of a code change. [1]

## 2. [future-agi / traceAI](https://github.com/future-agi/traceai)

- The Concept: A vendor-agnostic AI tracing and code observability framework built natively on OpenTelemetry. It bridges the gap between raw backend tracing and agentic tool usage. [2]
- How it uses OTel/Traces: It captures distributed transaction graphs, spans, and dependencies across multi-language frameworks (Python, TS, Java, .NET). A Business Analyst agent can query `traceAI` metadata to analyze how downstream components react to edge-case executions, rendering hidden, decoupled logic visible to the model. [2]

## 3. [agamm / awesome-ai-sre](https://github.com/agamm/awesome-ai-sre)

- The Concept: A curated directory indexing open-source autonomous engineering frameworks, AI SRE tools, and debugging agents.
- How it uses OTel/Traces: This repository serves as a centralized map for projects like HolmesGPT and K8sGPT, which swallow OpenTelemetry trace collectors, logs, and metrics directly into an internal LLM reasoning loop. Tracking this repo is the best way to monitor emerging tools designed to feed raw infrastructure traces into autonomous coding workflows. [3]

## 4. [o11y-dev / opentelemetry-skill](https://github.com/o11y-dev/opentelemetry-skill)

- The Concept: A specialized cognitive architecture designed specifically for AI coding agents (like Claude Code or Cursor) to handle complex telemetry data without suffering context overload.
- How it uses OTel/Traces: Instead of dumping an entire multi-megabyte OTel trace JSON into Claude's prompt—which triggers Sonnet's "Early Stop" bias—this framework acts as a cognitive router. It handles OTTL (OpenTelemetry Transformation Language) scripts to compress and filter raw distributed trace spans into logical tokens an AI agent can cleanly digest to find bugs or architectural dependencies. [4]

## 5. [ImmersiveFusion / opentelemetry-tracegen](https://github.com/ImmersiveFusion/opentelemetry-tracegen)

- The Concept: A high-fidelity distributed trace and topology generator.
- How it uses OTel/Traces: When engineering a Business Analyst agent pipeline, training the agent to properly query an OTel backend requires deterministic telemetry to practice on. `opentelemetry-tracegen` creates complex, topology-rich OTLP traces simulating modern asynchronous platforms, complete with full GenAI semantic conventions, making it a valuable sandbox for testing agentic blast-radius calculations. [5]

---

## 💡 The Integration Strategy for ASP.NET / Brownfield Apps

If you want to tie an OpenTelemetry pipeline into Claude Code to analyze a brownfield application:

1. Collect via OTel Auto-Instrumentation: Let your ASP.NET Core or Framework tests run with the CLR Profiler active. Export the resulting data format to an open-source collector (like [SigNoz](https://signoz.io/) or a local [Jaeger](https://www.jaegertracing.io/) instance).
2. Hydrate the Graph: Feed those trace JSON logs into an instance of `code-graph-rag`. It will correlate the actual `SpanKind.SERVER` or `ActivitySource` execution paths directly with your source files.
3. The BA Agent Pinning Step: When you request a code change, your BA agent queries the populated Graph DB first. It will output a concise bullet list of targeted files—verified by actual runtime execution—and feed _only_ those specific targets to Claude Code, bypassing the limitations of static code analysis. [1, 6, 7, 8]

Would you like to explore an architectural blueprint for how the BA Agent should automatically query a Graph DB using Cypher, or would you prefer a sample configuration file to stand up a local OpenTelemetry Collector sandbox to ingest your application's traces?

  

[1] [https://github.com](https://github.com/vitali87/code-graph-rag)

[2] [https://github.com](https://github.com/future-agi/traceai)

[3] [https://github.com](https://github.com/agamm/awesome-ai-sre)

[4] [https://github.com](https://github.com/o11y-dev/opentelemetry-skill)

[5] [https://github.com](https://github.com/ImmersiveFusion/opentelemetry-tracegen)

[6] [https://github.com](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/trace/api.md)

[7] [https://github.com](https://github.com/open-telemetry/opentelemetry-dotnet/blob/main/docs/trace/README.md)

[8] [https://github.com](https://github.com/magsther/awesome-opentelemetry)
