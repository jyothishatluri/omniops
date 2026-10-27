"""The runner the Slack layer calls: the real investigation and chat flows behind one small object.

Retrospective lessons encoded here:
- D9: every alert, mention, DM and slash command runs the same fixed flows (no model picker); this
  object is the only switch point, so Slack, the CLI and the benchmarks cannot drift apart.
- 3.3: a benchmark can pin one clock for the whole run (``anchor``); both flows resolve their time
  window against it instead of the wall clock.
- 4.5: the flows are imported on first use, so building the Slack app has no import side effects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omniops.agent.context import Deps
    from omniops.agent.types import Alert, RunResult
    from omniops.engine.timeutil import TimeAnchor
    from omniops.slack.status import StatusTrail


class DefaultRunner:
    """Implements ``slack.app.Runner``: ``build_app(deps, runner=DefaultRunner())``."""

    def __init__(self, anchor: TimeAnchor | None = None) -> None:
        self.anchor = anchor

    async def investigate(self, alert: Alert, deps: Deps, *, status: StatusTrail | None = None,
                          thread: str | None = None, private: bool = False) -> RunResult:
        """``private``: a /investigate preview (D12); its findings reach shared memory only when shared."""
        from omniops.agent.orchestrator import investigate

        return await investigate(alert, deps, status=status, thread=thread, anchor=self.anchor, private=private)

    async def answer(self, question: str, deps: Deps, *, thread: str | None = None,
                     status: StatusTrail | None = None) -> RunResult:
        from omniops.agent.chat import answer

        return await answer(question, deps, thread=thread, status=status, anchor=self.anchor)
