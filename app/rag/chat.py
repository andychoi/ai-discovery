"""Interactive RAG chat session against a discovery database."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# Enable readline history/editing (arrow keys, Ctrl+R) when available
try:
    import readline as _readline  # noqa: F401  (side-effect: activates line editing)
    _readline.set_history_length(200)
except ImportError:
    pass  # Windows without pyreadline — degrades gracefully

from .retriever import dual_search

console = Console()

_SYSTEM_PROMPT = """\
You are a codebase assistant. You answer questions about a software project using
retrieved context from two sources:
  [CODE] — raw code chunks (functions, classes, methods)
  [DOC]  — synthesized SDLC documents (as-is assessment, functional spec, API contracts, data schema)

Rules:
- Base your answer strictly on the provided context. If the context is insufficient, say so.
- When referencing code, mention the qualified name (e.g. `AuthService.Login`).
- When referencing a document section, mention the doc type and section heading.
- Be concise but complete. Use markdown formatting.
"""

_EXIT_COMMANDS = {"exit", "quit", "q", ":q"}


def _build_context(results: list[dict], max_chars: int = 6000) -> str:
    """Format retrieval results into a context block for the LLM prompt."""
    lines: list[str] = []
    total = 0
    for r in results:
        if r["source"] == "code":
            header = f"[CODE] {r['qualified_name']} ({r.get('chunk_type', '')}  domain={r.get('domain', '')})"
        else:
            header = f"[DOC] {r['doc_type']} › {r.get('section_heading', '')}  (doc_id={r['doc_id']})"
        block = f"{header}\n{r['chunk_text']}\n"
        if total + len(block) > max_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n---\n".join(lines)


def _build_prompt(history: list[dict], question: str, context: str) -> str:
    """Build full prompt: system + history + context + question."""
    parts = [_SYSTEM_PROMPT, "\n## Retrieved Context\n", context, "\n## Conversation\n"]
    for turn in history[-6:]:  # keep last 3 exchanges (6 messages)
        role = "User" if turn["role"] == "user" else "Assistant"
        parts.append(f"{role}: {turn['content']}")
    parts.append(f"User: {question}")
    parts.append("Assistant:")
    return "\n".join(parts)


def run_repl(
    db_path: Path,
    docs_dir: Path,
    llm_client,
    top_k: int = 5,
    embed_docs_first: bool = True,
    llm_tier: str = "tier1",
) -> None:
    """Run the interactive chat REPL."""
    from .doc_embedder import embed_docs

    console.print(Panel(
        f"[bold cyan]Discovery Chat[/]\n"
        f"[dim]DB: {db_path}  model: {llm_tier}[/]\n"
        f"Type [bold]exit[/] to quit.",
        expand=False,
    ))

    # Embed docs on first run (fast — only a few files)
    if embed_docs_first and docs_dir.exists():
        with console.status("[dim]Indexing generated docs...[/]"):
            result = embed_docs(docs_dir, db_path, llm_client)
        if result.get("resumed"):
            console.print(
                f"[dim]Docs already indexed ({result['files']} docs, {result['embedded']} chunks) — skipped.[/]"
            )
        elif result["embedded"] > 0:
            console.print(
                f"[dim]Indexed {result['files']} docs → {result['embedded']} chunks "
                f"(dim={result['dim']})[/]"
            )

    history: list[dict] = []

    while True:
        try:
            question = input("\nYou: ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/]")
            break

        if question.strip().lower() in _EXIT_COMMANDS:
            console.print("[dim]Bye.[/]")
            break
        if not question.strip():
            continue

        with console.status("[dim]Searching...[/]"):
            results = dual_search(question, db_path, llm_client, top_k=top_k)

        if not results:
            console.print("[yellow]No relevant context found in the database.[/]")
            continue

        # Show source summary
        code_hits = sum(1 for r in results if r["source"] == "code")
        doc_hits = sum(1 for r in results if r["source"] == "doc")
        console.print(
            f"[dim]  Retrieved {code_hits} code chunk(s), {doc_hits} doc section(s)[/]"
        )

        context = _build_context(results)
        prompt = _build_prompt(history, question, context)

        with console.status("[dim]Thinking...[/]"):
            response = llm_client.invoke(llm_tier, prompt, max_tokens=2048)

        answer = response.text.strip()
        console.print()
        console.print(Panel(Markdown(answer), title="[bold cyan]Assistant[/]", border_style="cyan"))

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
