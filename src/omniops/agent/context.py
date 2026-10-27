"""Process-wide dependencies and per-run context shared by every agent module.

The flows build ``Deps`` with the run-scoped LLM view (``deps.llm.for_run(run_id)``, see
``orchestrator.run_scoped``) before ``RunContext.create``, so ``ctx.deps.llm`` is that run's view.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from omniops.agent.types import Alert, RunStats, ToolEvent
from omniops.engine.catalog import Catalog
from omniops.engine.engine import FetchCache, SQLEngine
from omniops.engine.timeutil import TimeAnchor
from omniops.settings import Settings

if TYPE_CHECKING:
    from omniops.agent.evidence import EvidenceBoard
    from omniops.agent.hypotheses import HypothesisTracker
    from omniops.agent.memory import MemoryStore
    from omniops.llm.base import LLMClient
    from omniops.slack.status import StatusTrail

log = logging.getLogger("omniops")


def keep_ends(text: str, limit: int) -> str:
    """Clip a Slack thread for a prompt: keep the root line and the NEWEST replies, mark what was dropped.

    Head-clipping a thread loses exactly the replies that matter most (the latest human findings).
    """
    if len(text) <= limit:
        return text
    first, _, rest = text.partition("\n")
    first = first[: limit // 3]
    tail = rest[-max(0, limit - len(first) - 60):]
    return f"{first}\n[... {len(rest) - len(tail)} chars of older replies omitted ...]\n{tail}"


@dataclass
class Deps:
    catalog: Catalog
    llm: LLMClient
    settings: Settings = field(default_factory=Settings.from_env)
    memory: MemoryStore | None = None


@dataclass
class RunContext:
    run_id: str
    deps: Deps
    anchor: TimeAnchor
    engine: SQLEngine
    cache: FetchCache
    board: EvidenceBoard
    tracker: HypothesisTracker
    deadline: float
    alert: Alert | None = None
    question: str | None = None
    thread_context: str | None = None
    status: StatusTrail | None = None
    stats: RunStats = field(default_factory=RunStats)
    tool_events: list[ToolEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    specialist_verdicts: list[Any] = field(default_factory=list)
    stop_requested: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    _engines: list[SQLEngine] = field(default_factory=list)

    @classmethod
    def create(cls, deps: Deps, anchor: TimeAnchor, budget_s: float, *, alert: Alert | None = None,
               question: str | None = None, run_id: str | None = None) -> RunContext:
        from omniops.agent.evidence import EvidenceBoard
        from omniops.agent.hypotheses import HypothesisTracker

        cache = FetchCache()
        board = EvidenceBoard()
        engine = SQLEngine(deps.catalog, anchor, cache=cache, write_mode=deps.settings.write_mode)
        ctx = cls(run_id=run_id or uuid.uuid4().hex[:10], deps=deps, anchor=anchor, engine=engine, cache=cache,
                  board=board, tracker=HypothesisTracker(board), deadline=time.monotonic() + budget_s,
                  alert=alert, question=question)
        ctx._engines.append(engine)
        ctx.tracker.bind(ctx)          # loud zeros reach ctx.warnings; anti-anchoring reads the alert text
        return ctx

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def engine_for(self, agent: str, tables: set[str] | None = None) -> SQLEngine:
        """A separate DuckDB connection with a scoped catalog; API fetches are shared via the cache."""
        cat = self.deps.catalog.scoped(tables) if tables else self.deps.catalog
        eng = SQLEngine(cat, self.anchor, cache=self.cache, write_mode=self.deps.settings.write_mode)
        self._engines.append(eng)
        return eng

    def warn(self, msg: str, *, shadow: bool = False) -> None:
        """Loud zeros: a step that produced nothing is logged as a warning and kept on the run.

        ``shadow``: the warning comes from a shadow agent (D8): it is kept for traces in
        ``extra["shadow_warnings"]`` and never reaches the run warnings the user and the writer see.
        """
        log.warning("[%s] %s%s", self.run_id, "shadow: " if shadow else "", msg)
        if shadow:
            self.extra.setdefault("shadow_warnings", []).append(msg)
            return
        self.warnings.append(msg)

    def close(self) -> None:
        for e in self._engines:
            e.close()
