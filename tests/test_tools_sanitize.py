"""The deterministic SQL sanitizer: each known model mistake is fixed in code and reported."""

from __future__ import annotations

import pytest
import sqlglot

from omniops.agent.sanitize import SQLSanitizeError, sanitize_sql

COLS = {"service", "status", "message", "labels", "ts", "value"}


def test_clean_sql_is_untouched() -> None:
    sql = "SELECT service, count(*) AS n FROM loki_logs WHERE service = 'api' GROUP BY service"
    assert sanitize_sql(sql, COLS) == (sql, [])


def test_wrappers_are_stripped() -> None:
    fenced, fixes = sanitize_sql("```sql\nSELECT 1 AS x;\n```", COLS)
    assert fenced == "SELECT 1 AS x" and fixes == ["removed markdown code fence", "removed trailing semicolon"]
    labelled, fixes = sanitize_sql("sql: SELECT 1 AS x;;", COLS)
    assert labelled == "SELECT 1 AS x" and "removed leading 'sql' label" in fixes
    bare, fixes = sanitize_sql("SQL SELECT 2 AS y", COLS)
    assert bare == "SELECT 2 AS y" and fixes == ["removed leading 'sql' label"]


def test_double_quoted_literals_become_strings_unless_they_are_columns() -> None:
    sql, fixes = sanitize_sql('SELECT "service" FROM t WHERE "service" == "checkout" AND status IN ("500", "503")',
                              COLS)
    assert sql == "SELECT \"service\" FROM t WHERE \"service\" = 'checkout' AND status IN ('500', '503')"
    assert fixes == ["'==' -> '='", 'double-quoted literal "checkout" -> \'checkout\' (double quotes are identifiers)',
                     'double-quoted literal "500" -> \'500\' (double quotes are identifiers)',
                     'double-quoted literal "503" -> \'503\' (double quotes are identifiers)']
    # A known column on the right-hand side stays an identifier (a column-to-column comparison).
    same, fixes = sanitize_sql('SELECT * FROM t WHERE value > "value"', COLS)
    assert same == 'SELECT * FROM t WHERE value > "value"' and fixes == []
    # Aliases defined in the statement are identifiers too.
    alias, _ = sanitize_sql('SELECT value AS v FROM t WHERE value = "v"', COLS)
    assert alias.endswith('= "v"')
    json_key, fixes = sanitize_sql('SELECT labels->>"job" AS job FROM t', COLS)
    assert json_key == "SELECT labels->>'job' AS job FROM t" and fixes == ['JSON key "job" -> \'job\'']


def test_python_literals_and_smart_quotes() -> None:
    sql, fixes = sanitize_sql("SELECT * FROM t WHERE ok = True AND gone = False AND a = None AND b != None "
                              "AND c IS NOT None", COLS)
    assert sql == ("SELECT * FROM t WHERE ok = TRUE AND gone = FALSE AND a IS NULL AND b IS NOT NULL "
                   "AND c IS NOT NULL")
    assert fixes == ["Python True -> TRUE", "Python False -> FALSE", "'= None' -> 'IS NULL'",
                     "'!= None' -> 'IS NOT NULL'", "Python None -> NULL"]
    smart, fixes = sanitize_sql("SELECT * FROM t WHERE message = ‘can’t connect’ AND "
                                "service = “checkout”", COLS)
    # Delimiters become ASCII; the in-word apostrophe is content and is kept as typed.
    assert smart == "SELECT * FROM t WHERE message = 'can\u2019t connect' AND service = 'checkout'"
    assert fixes[0] == "smart quotes -> ASCII quotes" and len(fixes) == 2
    # Smart quotes inside an ASCII string are content, not delimiters.
    kept, fixes = sanitize_sql("SELECT * FROM t WHERE message = 'it’s down'", COLS)
    assert kept == "SELECT * FROM t WHERE message = 'it’s down'" and fixes == []


def test_output_parses_and_bad_input_is_rejected_with_a_lesson() -> None:
    sql, _ = sanitize_sql('```\nSELECT ts, value FROM t WHERE service == "api" AND ok = True;\n```', COLS)
    assert sqlglot.parse_one(sql, read="duckdb") is not None
    with pytest.raises(SQLSanitizeError) as multi:
        sanitize_sql("SELECT 1; DROP TABLE t", COLS)
    assert "separate run_sql calls" in multi.value.lesson
    with pytest.raises(SQLSanitizeError) as empty:
        sanitize_sql("```sql\n```", COLS)
    assert "one complete SELECT" in empty.value.lesson
    # Unparseable SQL is passed through (the engine reports the parse error with its own lesson).
    assert sanitize_sql("SELECT 'unterminated", COLS)[0] == "SELECT 'unterminated"
