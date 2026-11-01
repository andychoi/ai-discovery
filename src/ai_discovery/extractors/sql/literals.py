"""SQL text inside source code (roadmap item M6.8).

Only string literals that *start* like a SQL statement are analysed (`SELECT`, `INSERT`, `UPDATE`, `DELETE`,
`MERGE`, `WITH`, `CREATE`, `ALTER`, `EXEC` …). Adjacent pieces joined by `+` are concatenated; a non-literal
piece (`" + table + "`), an interpolation (`$"…{t}…"`, `` `…${t}…` ``, f-strings) becomes the placeholder
`__dyn__`, so a table whose name is computed is recognisable — and skipped — downstream.
"""

from __future__ import annotations

import re

DYN = "__dyn__"
# A statement *shape*, not just a leading verb: "Create a catalog item" / "Delete item" are UI text.
_SQL_SHAPE = re.compile(
    r"^\s*(?:SELECT\b[\s\S]*?\bFROM\b|INSERT\s+INTO\b|UPDATE\s+[\w.\[\]\"`]+(?:\s+\w+)?\s+SET\b"
    r"|DELETE\s+FROM\b|DELETE\s+[\w.\[\]\"`]+\s+WHERE\b|MERGE\s+INTO\b"
    r"|(?:CREATE(?:\s+OR\s+REPLACE)?|ALTER|DROP)\s+(?:TABLE|VIEW|INDEX|UNIQUE|SCHEMA|SEQUENCE)\b"
    r"|TRUNCATE\s+TABLE\b|WITH\s+\w+(?:\s*\([^)]*\))?\s+AS\s*\()", re.I)
_UPPER_VERB = re.compile(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|WITH)\b")
_SQL_PUNCT = re.compile(r"[*=,()@:?\[\]\"]|\$\d")
# JPQL / HQL is not SQL: its FROM names entities, not tables (`@Query` without nativeQuery, createQuery, @NamedQuery)
_JPQL_BEFORE = re.compile(r"(?:@Query\s*\(\s*(?:value\s*=\s*)?|\bcreateQuery\s*\(\s*|@NamedQuery\s*\([^)]*?\bquery\s*=\s*)$")
_NATIVE = re.compile(r"nativeQuery\s*=\s*true")


def looks_like_sql(text: str) -> bool:
    """A statement shape, written either with upper-case keywords or with SQL punctuation."""
    return bool(_SQL_SHAPE.match(text)) and bool(_UPPER_VERB.match(text) or _SQL_PUNCT.search(text))


def _jpql(source: str, start: int, end: int) -> bool:
    if not _JPQL_BEFORE.search(source[max(0, start - 160):start]):
        return False
    close = source.find(")", end)
    return not _NATIVE.search(source[end:close if close != -1 else end + 200])
# One literal: optional prefix ($, @, $@, @$, f, r, rf …) then a quoted body. Triple quotes first.
_LITERAL = re.compile(
    r'(?P<prefix>[$@fFrRbBuU]{0,3})(?:'
    r'"""(?P<tq>.*?)"""'
    r'|(?<=@)"(?P<vq>(?:[^"]|"")*)"'     # C# verbatim @"…" spans lines; "" escapes a quote
    r"|'''(?P<tq1>.*?)'''"
    r'|"(?P<dq>(?:[^"\\\n]|\\.|"")*)"'
    r"|`(?P<bq>(?:[^`\\]|\\.)*)`"
    r")", re.S)
_JOIN = re.compile(r"^\s*\+\s*$")               # "a" + "b"
_INTERP_BRACE = re.compile(r"\$\{[^}]*\}|\{[^{}]*\}")


def _body(m: re.Match) -> tuple[str, bool]:
    prefix = (m.group("prefix") or "").lower()
    text = next(g for g in (m.group("tq"), m.group("vq"), m.group("tq1"), m.group("dq"), m.group("bq"))
                if g is not None)
    verbatim = m.group("vq") is not None
    if verbatim:
        text = text.replace('""', '"')
    interpolated = "$" in prefix or "f" in prefix or (m.group("bq") is not None and "${" in text)
    if interpolated:
        text = _INTERP_BRACE.sub(DYN, text)
    elif m.group("dq") is not None and not verbatim:
        text = text.replace('\\"', '"').replace("\\n", "\n").replace("\\t", " ")
    return text, interpolated


def sql_literals(source: str) -> list[str]:
    """SQL statements written as string literals in *source* (concatenations joined; JPQL skipped)."""
    out: list[str] = []
    pieces: list[str] = []
    last_end = None
    for m in _LITERAL.finditer(source or ""):
        text, _ = _body(m)
        if _jpql(source, m.start(), m.end()):
            text = ""   # an entity query, not SQL
        between = source[last_end:m.start()] if last_end is not None else None
        if pieces and between is not None and _JOIN.match(between.replace("\n", " ")):
            pieces.append(text)                                   # "SELECT … " + "FROM t"
        elif pieces and between is not None and re.match(r"^\s*\+\s*[\w.()\[\]]+\s*\+\s*$", between.replace("\n", " ")):
            pieces += [DYN, text]                                 # "… FROM " + table + " WHERE …"
        else:
            if pieces:
                out.append("".join(pieces))
            pieces = [text]
        last_end = m.end()
    if pieces:
        out.append("".join(pieces))
    return [s for s in out if looks_like_sql(s)]
