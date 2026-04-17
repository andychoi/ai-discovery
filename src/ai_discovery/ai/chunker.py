"""AST-aware code chunker.

Splits CodeNodes into LLM-sized CodeChunks at semantic boundaries
(class→methods, etc.) rather than arbitrary character limits.
"""

from __future__ import annotations

import re
from ..graph.models import CodeNode, CodeChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_METHOD_RE = re.compile(
    r"^([ \t]+)"           # leading indent (group 1)
    r"(?:(?:@\w[^\n]*\n\1)*)"  # optional decorators at same indent
    r"def\s+(\w+)\s*\(",    # def name(
    re.MULTILINE,
)

_JAVA_METHOD_RE = re.compile(
    r"^([ \t]+)"                                    # indent
    r"(?:(?:public|private|protected|static|final|abstract|synchronized)\s+)*"
    r"\w[\w<>\[\],\s]*?\s+(\w+)\s*\(",              # return_type name(
    re.MULTILINE,
)


def _extract_class_header(source: str, max_lines: int = 10) -> str:
    """Return the first *max_lines* lines of a class as context prefix."""
    lines = source.split("\n")
    return "\n".join(lines[:max_lines])


def _split_class_into_methods(source: str, language: str) -> list[tuple[str, str]]:
    """Return list of (method_name, method_source) for a class body.

    Falls back to returning the entire source as one block if no methods found.
    """
    pattern = _METHOD_RE if language == "python" else _JAVA_METHOD_RE

    matches = list(pattern.finditer(source))
    if not matches:
        return [("__body__", source)]

    results: list[tuple[str, str]] = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source)
        method_name = m.group(2)
        results.append((method_name, source[start:end].rstrip()))
    return results


def _token_estimate(text: str) -> int:
    return len(text) // 4


def _make_chunk(
    text: str,
    index: int,
    node: CodeNode,
    *,
    chunk_type: str | None = None,
    qualified_name: str | None = None,
    parent_class: str | None = None,
) -> CodeChunk:
    return CodeChunk(
        text=text,
        chunk_index=index,
        chunk_type=chunk_type or node.node_type,
        file_path=node.file_path,
        language=node.language,
        qualified_name=qualified_name or node.qualified_name,
        parent_class=parent_class,
        domain=node.domain,
        node_type=node.node_type,
        annotations=list(node.annotations),
        calls=list(node.calls),
        framework_hints=dict(node.framework_hints),
        token_estimate=_token_estimate(text),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_code_nodes(
    nodes: list[CodeNode],
    max_chars: int = 6000,
    rag_chunk_size: int = 1500,
    rag_overlap: int = 200,
) -> list[CodeChunk]:
    """Split CodeNodes into LLM-sized CodeChunks.

    Rules:
    1. If node source_code fits in *max_chars* → single chunk.
    2. If class too large → split: class header (first ~10 lines) as context
       prefix, then each method body as a separate chunk prefixed with header.
    3. Endpoint/function → single chunk (metadata preserved).
    4. Each chunk carries: qualified_name, domain, node_type, annotations,
       calls, framework_hints.
    5. chunk_type mirrors node_type.
    6. token_estimate ≈ len(text) // 4.
    """
    chunks: list[CodeChunk] = []
    idx = 0

    for node in nodes:
        src = node.source_code or ""

        # Rule 1 & 3: fits in a single chunk
        if len(src) <= max_chars or node.node_type != "class":
            chunks.append(_make_chunk(src, idx, node))
            idx += 1
            continue

        # Rule 2: large class → split by method
        header = _extract_class_header(src)
        methods = _split_class_into_methods(src, node.language)

        if len(methods) <= 1:
            # Could not split further; emit as single chunk
            chunks.append(_make_chunk(src, idx, node))
            idx += 1
            continue

        for method_name, method_src in methods:
            text = f"{header}\n    # ...\n\n{method_src}"
            qn = f"{node.qualified_name}.{method_name}"
            chunks.append(
                _make_chunk(
                    text,
                    idx,
                    node,
                    chunk_type="method",
                    qualified_name=qn,
                    parent_class=node.qualified_name,
                )
            )
            idx += 1

    return chunks


def chunk_for_rag(
    chunks: list[CodeChunk],
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[CodeChunk]:
    """Sub-split larger chunks into RAG-sized pieces with overlap.

    For each input chunk:
    - If text <= chunk_size → keep as-is.
    - If text > chunk_size → split at line boundaries with *overlap* chars.
    - Preserve all metadata from parent chunk.
    - Update chunk_index sequentially.
    """
    result: list[CodeChunk] = []
    idx = 0

    for chunk in chunks:
        if len(chunk.text) <= chunk_size:
            result.append(CodeChunk(
                text=chunk.text,
                chunk_index=idx,
                chunk_type=chunk.chunk_type,
                file_path=chunk.file_path,
                language=chunk.language,
                qualified_name=chunk.qualified_name,
                parent_class=chunk.parent_class,
                domain=chunk.domain,
                node_type=chunk.node_type,
                annotations=list(chunk.annotations),
                calls=list(chunk.calls),
                called_by=list(chunk.called_by),
                framework_hints=dict(chunk.framework_hints),
                token_estimate=chunk.token_estimate,
            ))
            idx += 1
            continue

        # Split at line boundaries
        lines = chunk.text.split("\n")
        current_lines: list[str] = []
        current_len = 0

        i = 0
        while i < len(lines):
            line = lines[i]
            line_len = len(line) + 1  # +1 for newline

            if current_len + line_len > chunk_size and current_lines:
                # Emit current sub-chunk
                sub_text = "\n".join(current_lines)
                result.append(CodeChunk(
                    text=sub_text,
                    chunk_index=idx,
                    chunk_type=chunk.chunk_type,
                    file_path=chunk.file_path,
                    language=chunk.language,
                    qualified_name=chunk.qualified_name,
                    parent_class=chunk.parent_class,
                    domain=chunk.domain,
                    node_type=chunk.node_type,
                    annotations=list(chunk.annotations),
                    calls=list(chunk.calls),
                    called_by=list(chunk.called_by),
                    framework_hints=dict(chunk.framework_hints),
                    token_estimate=_token_estimate(sub_text),
                ))
                idx += 1

                # Backtrack for overlap
                overlap_len = 0
                backtrack = len(current_lines) - 1
                while backtrack >= 0 and overlap_len < overlap:
                    overlap_len += len(current_lines[backtrack]) + 1
                    backtrack -= 1
                backtrack += 1  # move forward one (was one too far)

                current_lines = current_lines[backtrack:]
                current_len = sum(len(l) + 1 for l in current_lines)
            else:
                current_lines.append(line)
                current_len += line_len
                i += 1

        # Emit remaining
        if current_lines:
            sub_text = "\n".join(current_lines)
            result.append(CodeChunk(
                text=sub_text,
                chunk_index=idx,
                chunk_type=chunk.chunk_type,
                file_path=chunk.file_path,
                language=chunk.language,
                qualified_name=chunk.qualified_name,
                parent_class=chunk.parent_class,
                domain=chunk.domain,
                node_type=chunk.node_type,
                annotations=list(chunk.annotations),
                calls=list(chunk.calls),
                called_by=list(chunk.called_by),
                framework_hints=dict(chunk.framework_hints),
                token_estimate=_token_estimate(sub_text),
            ))
            idx += 1

    return result
