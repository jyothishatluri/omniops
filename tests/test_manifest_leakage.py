"""Model-visible text must not encode a benchmark, and must teach generic rules instead.

Source manifests, API templates and agent prompts are what the model reads (descriptions, pitfalls,
examples, filter examples, query-language notes, prompt rules). They must help any Grafana /
Prometheus / Loki / Tempo / Kubernetes deployment, so they may not carry benchmark names, benchmark
task ids or task dashboard uids, or phrasing copied from a benchmark answer key. Only the benchmark
adapters (``src/omniops/bench/**``) and ``docs/BENCH_*.md`` may name the benchmarks; neither is
model-visible, so neither is scanned here.

The task ids, dashboard uids and answer keys are read from the vendored benchmark checkouts next to
the repository; those checks skip when the checkouts are absent.

The Grafana guidance tests pin the generic dashboard rules that replaced benchmark-shaped hints:
range summaries are computed in the query, multi-value variables only go in regex matchers, and
Grafana's built-in interval variables are left to Grafana instead of being substituted by hand.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import pytest
import yaml

from omniops.engine.loader import load_manifest_file

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "omniops"
VENDOR = ROOT.parent / "vendor"
O11Y_TASKS = VENDOR / "o11y-bench" / "tasks-spec"
ARENA_FAULTS = VENDOR / "project-arena" / "scenarios" / "faults"

BENCHMARK_NAMES = ("o11y-bench", "o11ybench", "Edge Delta Arena", "project-arena")
NGRAM = 6  # an answer-key cause leaks when any run of this many consecutive words is reproduced
_EDGE_PUNCTUATION = ".,;:!?()[]{}<>\"'`*_|#-"  # stripped from both ends of a word


def _scanned_files() -> list[Path]:
    files = [*(PKG / "sources" / "manifests").glob("*.yaml"), *(PKG / "sources" / "templates").glob("*.yaml"),
             *(PKG / "agent" / "prompts").glob("*.md")]
    return sorted(files)


SCANNED = _scanned_files()


def _ids(paths: list[Path]) -> list[str]:
    return [str(p.relative_to(PKG)) for p in paths]


@cache
def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _words(text: str) -> list[str]:
    """Lower-cased words with surrounding punctuation removed (quotes, commas, YAML/Markdown marks)."""
    out = []
    for raw in text.lower().split():
        word = raw.strip(_EDGE_PUNCTUATION)
        if word:
            out.append(word)
    return out


def _ngrams(words: list[str], n: int = NGRAM) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


@cache
def _o11y_identifiers() -> frozenset[str]:
    """Every o11y-bench task id and every dashboard uid a task provisions."""
    out: set[str] = set()
    for spec in O11Y_TASKS.glob("*/*.yaml"):
        out.add(spec.stem)
        data = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
        if data.get("id"):
            out.add(str(data["id"]))
        for dash in data.get("setup_dashboards") or []:
            if isinstance(dash, dict) and dash.get("uid"):
                out.add(str(dash["uid"]))
    return frozenset(out)


@cache
def _arena_causes() -> tuple[tuple[str, str], ...]:
    out = []
    for key in sorted(ARENA_FAULTS.glob("*/answer-key.json")):
        cause = ((json.loads(key.read_text(encoding="utf-8")) or {}).get("ground_truth") or {}).get("cause")
        if cause:
            out.append((key.parent.name, str(cause)))
    return tuple(out)


def _identifier_hits(text: str, identifiers: frozenset[str]) -> list[str]:
    """Identifiers present as whole tokens (not as part of a longer hyphenated/underscored name)."""
    hits = []
    for ident in sorted(identifiers):
        if re.search(rf"(?<![\w-]){re.escape(ident)}(?![\w-])", text, re.IGNORECASE):
            hits.append(ident)
    return hits


def _cause_hits(text: str, causes: tuple[tuple[str, str], ...]) -> list[str]:
    grams = _ngrams(_words(text))
    hits = []
    for fault, cause in causes:
        shared = _ngrams(_words(cause)) & grams
        if shared:
            hits.append(f"{fault}: {' '.join(min(shared))!r}")
    return hits


# ---------------------------------------------------------------------------------------------- helpers


def test_scan_covers_manifests_templates_and_prompts() -> None:
    names = set(_ids(SCANNED))
    assert "sources/manifests/grafana.yaml" in names
    assert "sources/templates/prometheus.yaml" in names
    assert any(n.startswith("agent/prompts/") for n in names)


def test_identifier_match_is_whole_token() -> None:
    ids = frozenset({"demo-board", "list-widgets"})
    assert _identifier_hits("uid = 'demo-board'", ids) == ["demo-board"]
    assert _identifier_hits("UID DEMO-BOARD", ids) == ["demo-board"]
    assert _identifier_hits("uid = 'my-demo-board-2'", ids) == []
    assert _identifier_hits("list widgets first", ids) == []


def test_cause_match_needs_a_run_of_consecutive_words() -> None:
    causes = (("demo", "In ns, worker-a sleeps 30 seconds then unconditionally exits 1."),)
    assert _cause_hits("- \"worker-a sleeps 30 seconds then unconditionally exits\"", causes)
    assert _cause_hits("pitfall: worker-a\n  sleeps 30 SECONDS, then unconditionally exits", causes)
    assert not _cause_hits("a process that sleeps 30 seconds and exits 1", causes)


# ---------------------------------------------------------------------------------------------- guards


@pytest.mark.parametrize("path", SCANNED, ids=_ids(SCANNED))
def test_no_benchmark_names(path: Path) -> None:
    text = _text(path).lower()
    found = [name for name in BENCHMARK_NAMES if name.lower() in text]
    assert not found, f"{path.relative_to(ROOT)} names a benchmark: {found}"


# The synthetic stack's own service, job and metric names: generic-looking, but as examples they hand the
# model the benchmark's topology. Manifests use neutral names (storefront-web, billing-api, ...).
BENCHMARK_STACK_NAMES = ("webapp", "api-gateway", "order-service", "payment-service", "user-service",
                         "auth-cache-refresh", "service_cache_refresh_lag_seconds", "service_retry_queue_depth")


@pytest.mark.parametrize("path", SCANNED, ids=_ids(SCANNED))
def test_no_benchmark_stack_names(path: Path) -> None:
    text = _text(path)
    found = [n for n in BENCHMARK_STACK_NAMES
             if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(n)}(?![A-Za-z0-9_])", text)]
    assert not found, f"{path.relative_to(ROOT)} uses the benchmark stack's names as examples: {found}"


@pytest.mark.skipif(not O11Y_TASKS.is_dir(), reason="o11y-bench task specs are not vendored")
@pytest.mark.parametrize("path", SCANNED, ids=_ids(SCANNED))
def test_no_o11y_task_ids_or_dashboard_uids(path: Path) -> None:
    identifiers = _o11y_identifiers()
    assert identifiers, "no task ids parsed from the vendored task specs"
    hits = _identifier_hits(_text(path), identifiers)
    assert not hits, f"{path.relative_to(ROOT)} contains benchmark task ids / dashboard uids: {hits}"


@pytest.mark.skipif(not ARENA_FAULTS.is_dir(), reason="Arena scenarios are not vendored")
@pytest.mark.parametrize("path", SCANNED, ids=_ids(SCANNED))
def test_no_arena_answer_key_phrasing(path: Path) -> None:
    causes = _arena_causes()
    assert causes, "no answer-key causes parsed from the vendored Arena scenarios"
    hits = _cause_hits(_text(path), causes)
    assert not hits, f"{path.relative_to(ROOT)} reproduces Arena answer-key causes: {hits}"


# ---------------------------------------------------------------------------------------------- Grafana guidance


def _grafana_pitfalls(table: str | None = None) -> list[str]:
    manifest = load_manifest_file(PKG / "sources" / "manifests" / "grafana.yaml")
    tables = [t for t in manifest.tables if table is None or t.name == table]
    assert tables, f"no grafana table {table!r}"
    return [p for t in tables for p in t.pitfalls] + ([] if table else list(manifest.pitfalls))


def test_grafana_dashboard_write_computes_range_summaries_in_the_query() -> None:
    text = " ".join(_grafana_pitfalls("grafana_dashboard_write"))
    assert "max_over_time(<gauge>[$__range])" in text
    assert '"instant": true' in text and '"range": false' in text
    assert "reduceOptions" in text  # names the display reducer it must not rely on


def test_grafana_multi_value_variables_stay_out_of_equality_matchers() -> None:
    for table in ("grafana_dashboard_write", "grafana_dashboard_variables"):
        text = " ".join(_grafana_pitfalls(table))
        assert "'=' " in text and "matches nothing" in text, table
        assert 'label=~"' in text, table


def test_grafana_builtin_interval_variables_are_not_substituted_by_hand() -> None:
    builtin = ("$__range", "$__rate_interval", "$__interval")
    about_builtins = [p for p in _grafana_pitfalls() if any(v in p for v in builtin)]
    assert about_builtins, "Grafana pitfalls should explain the built-in interval variables"
    by_hand = [p for p in about_builtins if re.search(r"substitut|literal value", p, re.IGNORECASE)]
    assert not by_hand, f"pitfalls tell the model to substitute Grafana variables by hand: {by_hand}"
    assert any("expand" in p and "$__range" in p for p in _grafana_pitfalls("grafana_ds_query"))
