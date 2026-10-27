"""Deterministic SQL sanitizer between the model and the engine.

Retrospective lessons encoded here:
- 3.10.1 Models misuse tool inputs in predictable, recurring ways; a schema-aware sanitizer fixes
         each known mistake in code (instead of "always do X" prompt rules) and reports what it
         changed so the model learns. Fixes: code fences, a leading "sql" label, trailing
         semicolons, smart quotes, ``==``, double-quoted strings used as literals (only when the
         name is not a known column), ``->> "key"`` JSON paths, Python ``None``/``True``/``False``.
- 3.10.4 Errors teach the right lesson: multiple statements are rejected with an explicit lesson.
- 3.1    Code fixes syntax; it never guesses meaning: anything ambiguous is left for the engine to
         reject with its own lesson.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

DIALECT = Dialect.get_or_raise("duckdb")

_FENCE = re.compile(r"```[ \t]*(?:sql|duckdb|postgresql|postgres)?[ \t]*\n?(.*?)(?:```|\Z)", re.IGNORECASE | re.DOTALL)
_LEADING_LABEL = re.compile(r"^\s*(?:sql|duckdb)\s*(?::\s*|\n\s*|\s+(?=(?:select|with)\b))", re.IGNORECASE)
_COMPARE = {TokenType.EQ, TokenType.NEQ, TokenType.LIKE, TokenType.ILIKE, TokenType.GT, TokenType.GTE,
            TokenType.LT, TokenType.LTE}
_JSON_ARROWS = {TokenType.ARROW, TokenType.DARROW}
_LSQ, _RSQ, _LDQ, _RDQ = "\u2018", "\u2019", "\u201c", "\u201d"
_SMART = {_LSQ: "'", _RSQ: "'", _LDQ: '"', _RDQ: '"'}


class SQLSanitizeError(ValueError):
    def __init__(self, message: str, lesson: str) -> None:
        super().__init__(message)
        self.lesson = lesson


def _closing_smart(sql: str, start: int, close: str) -> int:
    """Index of the closing smart quote; a right quote followed by a letter is an apostrophe (can\u2019t)."""
    j = sql.find(close, start)
    while j != -1 and j + 1 < len(sql) and sql[j + 1].isalnum():
        j = sql.find(close, j + 1)
    return j


def _replace_smart_quotes(sql: str) -> tuple[str, bool]:
    """Smart quotes used as delimiters become ASCII; smart quotes inside ASCII strings are content."""
    out: list[str] = []
    changed = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):                       # copy an ASCII-quoted span verbatim
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:   # doubled quote escape
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif ch in (_LSQ, _LDQ):
            q = _SMART[ch]
            j = _closing_smart(sql, i + 1, _RSQ if ch == _LSQ else _RDQ)
            if j == -1:
                out.append(q)
                i += 1
            else:
                out.append(q + sql[i + 1:j].replace(q, q + q) + q)
                i = j + 1
            changed = True
        elif ch in _SMART:
            out.append(_SMART[ch])
            changed = True
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), changed


def _strip_wrappers(sql: str, fixes: list[str]) -> str:
    m = _FENCE.search(sql)
    if m:
        sql = m.group(1)
        fixes.append("removed markdown code fence")
    if _LEADING_LABEL.match(sql):
        sql = _LEADING_LABEL.sub("", sql, count=1)
        fixes.append("removed leading 'sql' label")
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped.rstrip("; \t\r\n")
        fixes.append("removed trailing semicolon")
    return stripped


def _single_quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _in_list_starts(tokens: list[Token]) -> set[int]:
    """Indexes of the '(' that opens an IN (...) value list."""
    return {i + 1 for i, t in enumerate(tokens[:-1])
            if t.token_type == TokenType.IN and tokens[i + 1].token_type == TokenType.L_PAREN}


def _token_fixes(sql: str, tokens: list[Token], known: set[str], fixes: list[str]) -> str:
    edits: list[tuple[int, int, str]] = []        # (start, end inclusive, replacement)
    in_starts = _in_list_starts(tokens)
    depth_stack: list[bool] = []                  # per open paren: is it an IN list?
    for i, tok in enumerate(tokens):
        tt = tok.token_type
        prev = tokens[i - 1] if i else None
        if tt == TokenType.L_PAREN:
            depth_stack.append(i in in_starts)
        elif tt == TokenType.R_PAREN and depth_stack:
            depth_stack.pop()
        elif tt == TokenType.EQ and tok.text == "==":
            edits.append((tok.start, tok.end, "="))
            fixes.append("'==' -> '='")
        elif tt == TokenType.IDENTIFIER and prev is not None:
            raw = sql[tok.start:tok.end + 1]
            if not raw.startswith('"'):
                continue
            after_dot = prev.token_type == TokenType.DOT
            # "p".pod / "k8s_pods".id: a quoted table or alias qualifier is an identifier, never a literal.
            before_dot = i + 1 < len(tokens) and tokens[i + 1].token_type == TokenType.DOT
            in_list = bool(depth_stack and depth_stack[-1]) and prev.token_type in (TokenType.L_PAREN, TokenType.COMMA)
            if prev.token_type in _JSON_ARROWS:
                edits.append((tok.start, tok.end, _single_quoted(tok.text)))
                fixes.append(f'JSON key "{tok.text}" -> \'{tok.text}\'')
            elif not after_dot and not before_dot and (prev.token_type in _COMPARE or in_list) \
                    and tok.text.lower() not in known:
                edits.append((tok.start, tok.end, _single_quoted(tok.text)))
                fixes.append(f'double-quoted literal "{tok.text}" -> \'{tok.text}\' (double quotes are identifiers)')
        elif tt == TokenType.VAR and tok.text == "None" and "none" not in known:
            if prev is not None and prev.token_type == TokenType.EQ:
                edits.append((prev.start, tok.end, "IS NULL"))
                fixes.append("'= None' -> 'IS NULL'")
            elif prev is not None and prev.token_type == TokenType.NEQ:
                edits.append((prev.start, tok.end, "IS NOT NULL"))
                fixes.append("'!= None' -> 'IS NOT NULL'")
            else:
                edits.append((tok.start, tok.end, "NULL"))
                fixes.append("Python None -> NULL")
        elif tt in (TokenType.TRUE, TokenType.FALSE) and tok.text in ("True", "False"):
            edits.append((tok.start, tok.end, tok.text.upper()))
            fixes.append(f"Python {tok.text} -> {tok.text.upper()}")
    for start, end, rep in sorted(edits, reverse=True):
        sql = sql[:start] + rep + sql[end + 1:]
    return sql


def sanitize_sql(sql: str, known_columns: Iterable[str] = ()) -> tuple[str, list[str]]:
    """Return (sanitized SQL, list of fixes applied). Raises SQLSanitizeError for empty/multi-statement SQL.

    ``known_columns`` (any case) protects real double-quoted identifiers from literal conversion.
    Unparseable SQL is returned with the fixes made so far; the engine reports the parse error.
    """
    fixes: list[str] = []
    sql, smart = _replace_smart_quotes(sql or "")
    if smart:
        fixes.append("smart quotes -> ASCII quotes")
    sql = _strip_wrappers(sql, fixes)
    if not sql:
        raise SQLSanitizeError("empty SQL", "pass one complete SELECT statement in the sql argument")
    try:
        tokens = DIALECT.tokenize(sql)
    except SqlglotError:
        return sql, fixes
    semis = [t for t in tokens if t.token_type == TokenType.SEMICOLON]
    if semis:
        if any(t.token_type != TokenType.SEMICOLON for t in tokens[tokens.index(semis[0]) + 1:]):
            raise SQLSanitizeError("multiple SQL statements in one call",
                                  "send exactly one SELECT per run_sql call; independent queries go in "
                                  "separate run_sql calls in the same step")
        sql = sql[:semis[0].start].rstrip()
        tokens = tokens[:tokens.index(semis[0])]
        if "removed trailing semicolon" not in fixes:
            fixes.append("removed trailing semicolon")
    known = {c.lower() for c in known_columns}
    # Aliases and CTE names defined in this statement are identifiers too.
    known |= {tokens[i + 1].text.lower() for i, t in enumerate(tokens[:-1])
              if t.token_type == TokenType.ALIAS and tokens[i + 1].token_type in (TokenType.IDENTIFIER, TokenType.VAR)}
    known |= _table_names_and_aliases(tokens)
    return _token_fixes(sql, tokens, known, fixes), fixes


def _table_names_and_aliases(tokens: list[Token]) -> set[str]:
    """Table names after FROM/JOIN and the aliases written right after them without AS
    (``FROM loki_logs l JOIN k8s_pods "p"``): identifiers, never literals."""
    names = {TokenType.IDENTIFIER, TokenType.VAR}
    out: set[str] = set()
    for i, t in enumerate(tokens):
        if t.token_type not in (TokenType.FROM, TokenType.JOIN):
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].token_type in names:
            out.add(tokens[j].text.lower())
            if j + 1 < len(tokens) and tokens[j + 1].token_type == TokenType.DOT:   # schema.table
                j += 2
                continue
            if j + 1 < len(tokens) and tokens[j + 1].token_type in names:          # table alias
                out.add(tokens[j + 1].text.lower())
            break
    return out
