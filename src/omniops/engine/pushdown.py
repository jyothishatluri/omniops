"""SQL analysis: find virtual-table occurrences and the WHERE predicates that apply to each.

Every occurrence of a virtual table is planned separately, from the WHERE clause of the SELECT
that scans it. That is what lets one SQL statement compare the same table across two windows
(a baseline in a CTE, the incident window in the outer query) without the model issuing the same
query twice (OpsIntelligence 7.1 "repeat baselines").

Only top-level AND-ed conjuncts are pushed down. Anything else (OR across columns, functions of
columns) stays in SQL and is evaluated by DuckDB over the fetched rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlglot
from sqlglot import exp

from omniops.engine import timeutil

READ_DIALECT = "duckdb"


class SQLAnalysisError(ValueError):
    lesson = "fix the SQL syntax; use DuckDB SQL"


@dataclass
class Predicate:
    column: str
    op: str  # = != in like > >= < <= between
    value: Any
    node: exp.Expression  # the conjunct, so it can be stripped after pushdown
    pushed: bool = False  # set by the planner when the request actually carries this predicate


@dataclass
class Occurrence:
    table: str
    alias: str
    node: exp.Table
    select: exp.Select | None
    predicates: list[Predicate] = field(default_factory=list)
    unpushed: list[str] = field(default_factory=list)


_NOW_FUNCS = {"now", "current_timestamp", "get_current_timestamp", "transaction_timestamp"}
_ANCHOR_FUNCS = {"anchor_now": "now", "window_start": "window_start", "window_end": "window_end"}


def parse(sql: str) -> exp.Expression:
    try:
        trees = sqlglot.parse(sql, read=READ_DIALECT)
    except sqlglot.errors.ParseError as exc:
        raise SQLAnalysisError(f"SQL parse error: {exc}") from exc
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise SQLAnalysisError("send exactly one SQL statement per call")
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With, exp.Subquery)) and \
            not tree.find(exp.Select):
        raise SQLAnalysisError("only SELECT queries are allowed (writes go through staged approvals)")
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command):
        if tree.find(bad):
            raise SQLAnalysisError("only SELECT queries are allowed (writes go through staged approvals)")
    return tree


def _ts_literal(dt: datetime) -> exp.Expression:
    return exp.cast(exp.Literal.string(timeutil.fmt_rfc3339_ms(dt).replace("Z", "+00:00")), exp.DataType.build("TIMESTAMPTZ"))


def pin_relative_times(tree: exp.Expression, anchor: timeutil.TimeAnchor) -> exp.Expression:
    """Replace now()/current_timestamp/window_start()/window_end() with anchor literals.

    "Two days ago" means two days before the anchor, not before the wall clock.
    """
    def repl(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.CurrentTimestamp,)):
            return _ts_literal(anchor.now)
        if isinstance(node, exp.CurrentDate):
            return exp.cast(exp.Literal.string(anchor.now.date().isoformat()), exp.DataType.build("DATE"))
        if isinstance(node, exp.Anonymous) or isinstance(node, exp.Func):
            name = (node.name or "").lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()
            if name in _NOW_FUNCS:
                return _ts_literal(anchor.now)
            if name in _ANCHOR_FUNCS:
                which = _ANCHOR_FUNCS[name]
                start, end = anchor.default_window("1h")
                return _ts_literal({"now": anchor.now, "window_start": start, "window_end": end}[which])
        return node

    return tree.transform(repl)


def conjuncts(where: exp.Expression | None) -> list[exp.Expression]:
    """The top-level AND-ed conjuncts of a WHERE clause (parentheses flattened)."""
    return _conjuncts(where)


def _conjuncts(where: exp.Expression | None) -> list[exp.Expression]:
    if where is None:
        return []
    node = where.this if isinstance(where, exp.Where) else where
    out: list[exp.Expression] = []

    def walk(n: exp.Expression) -> None:
        if isinstance(n, exp.And):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, exp.Paren):
            walk(n.this)
        else:
            out.append(n)

    walk(node)
    return out


def _select_sources(select: exp.Select) -> list[exp.Table]:
    tables: list[exp.Table] = []
    frm = select.args.get("from") or select.args.get("from_")
    if frm is not None:
        for t in frm.find_all(exp.Table):
            if t.find_ancestor(exp.Select) is select:
                tables.append(t)
    for j in select.args.get("joins") or []:
        for t in j.find_all(exp.Table):
            if t.find_ancestor(exp.Select) is select:
                tables.append(t)
    return tables


def _is_constant(e: exp.Expression) -> bool:
    return e.find(exp.Column) is None and e.find(exp.Select) is None


class ConstantEvaluator:
    """Evaluates constant SQL expressions (literals, TIMESTAMP '...' - INTERVAL ...) via DuckDB."""

    def __init__(self, con: Any):
        self.con = con

    def __call__(self, e: exp.Expression) -> Any:
        if isinstance(e, exp.Literal):
            if e.is_string:
                return e.this
            try:
                return int(e.this)
            except ValueError:
                return float(e.this)
        if isinstance(e, exp.Boolean):
            return bool(e.this)
        if isinstance(e, exp.Null):
            return None
        if isinstance(e, exp.Neg) and isinstance(e.this, exp.Literal):
            return -self(e.this)
        sql = e.sql(dialect=READ_DIALECT)
        cur = self.con.cursor()
        try:
            rows = cur.execute(f"SELECT {sql}").fetchall()
        finally:
            cur.close()
        return rows[0][0] if rows else None


def _column_belongs(col: exp.Column, occ_alias: str, occ_table: str, columns_of: dict[str, set[str]],
                    sources_in_select: list[tuple[str, str]]) -> bool:
    qual = col.table
    if qual:
        return qual == occ_alias
    name = col.name
    if name not in columns_of.get(occ_table, set()):
        return False
    owners = [a for a, t in sources_in_select if name in columns_of.get(t, set())]
    return owners == [occ_alias] or len(sources_in_select) == 1


_CMP = {exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "=": "=", "!=": "!="}


def extract_occurrences(tree: exp.Expression, virtual: dict[str, set[str]], evaluate: ConstantEvaluator
                        ) -> list[Occurrence]:
    """Find each virtual table occurrence and the constant predicates that target it.

    ``virtual`` maps table name -> set of its column names.
    """
    cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    occs: list[Occurrence] = []
    for t in tree.find_all(exp.Table):
        name = t.name
        if not name or name in cte_names or name not in virtual or t.args.get("db"):
            continue
        alias = t.alias_or_name
        select = t.find_ancestor(exp.Select)
        occ = Occurrence(table=name, alias=alias, node=t, select=select)
        if select is not None:
            srcs = [(s.alias_or_name, s.name) for s in _select_sources(select)]
            for conj in _conjuncts(select.args.get("where")):
                pred = _match_predicate(conj, occ, virtual, srcs, evaluate)
                if pred is not None:
                    occ.predicates.append(pred)
                elif any(_column_belongs(c, alias, name, virtual, srcs) for c in conj.find_all(exp.Column)):
                    occ.unpushed.append(conj.sql(dialect=READ_DIALECT))
        occs.append(occ)
    return occs


def _match_predicate(conj: exp.Expression, occ: Occurrence, virtual: dict[str, set[str]],
                     srcs: list[tuple[str, str]], evaluate: ConstantEvaluator) -> Predicate | None:
    def own(c: exp.Expression) -> str | None:
        if isinstance(c, exp.Column) and _column_belongs(c, occ.alias, occ.table, virtual, srcs):
            return c.name
        return None

    if type(conj) in _CMP:
        op = _CMP[type(conj)]
        left, right = conj.left, conj.right
        col = own(left)
        if col and _is_constant(right):
            return Predicate(col, op, evaluate(right), conj)
        col = own(right)
        if col and _is_constant(left):
            return Predicate(col, _FLIP[op], evaluate(left), conj)
        return None
    if isinstance(conj, exp.In) and not conj.args.get("query"):
        col = own(conj.this)
        values = conj.expressions
        if col and values and all(_is_constant(v) for v in values):
            return Predicate(col, "in", [evaluate(v) for v in values], conj)
        return None
    if isinstance(conj, exp.Between):
        col = own(conj.this)
        lo, hi = conj.args.get("low"), conj.args.get("high")
        if col and lo is not None and hi is not None and _is_constant(lo) and _is_constant(hi):
            return Predicate(col, "between", (evaluate(lo), evaluate(hi)), conj)
        return None
    if isinstance(conj, (exp.Like, exp.ILike)) and not conj.args.get("negate"):
        # NOT LIKE parses as Like(negate=True): pushing it as LIKE would fetch the opposite rows.
        col = own(conj.this)
        if col and _is_constant(conj.expression):
            return Predicate(col, "like", evaluate(conj.expression), conj)
    return None


def strip_conjunct(node: exp.Expression) -> None:
    """Replace a pushed-down conjunct with TRUE (the API was authoritative for it)."""
    node.replace(exp.true())
