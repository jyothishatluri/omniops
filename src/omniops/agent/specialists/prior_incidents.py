"""Prior-incidents specialist: has this happened before, and did the prior trigger recur before this onset?

Retrospective lessons encoded here:
- 3.6.1 / Part 5 (Content API cold cache): a prior postmortem described the right mechanism but was
  only cited, never tested. Prior causes become hypotheses to TEST, with a mandatory recurrence probe:
  did the prior trigger class fire before this onset?
- 3.17: memory recall (similar incidents, known facts, ruled-out causes) is the first seed; human-named
  partitions rarely equal platform service names, so searches use the service and the alert name.
- 7.1: "why now?" - alert history over a week is summarised in code by clock time, so same-clock-time
  recurrence (a scheduled job) is visible before the model's first turn.
- 3.6.1: a human "known issue" reply is a lead to verify, never confirmation (playbook prompt).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from omniops.agent.specialists.base import SpecialistRun, SpecialistSpec

if TYPE_CHECKING:
    from omniops.agent.context import RunContext
    from omniops.engine.manifest import RoleBinding
    from omniops.engine.results import QueryResult

INCIDENT_LOOKBACK = timedelta(days=30)
CHAT_LOOKBACK = timedelta(days=14)
ALERT_HISTORY_LOOKBACK = timedelta(days=7)
_FIRING = ("firing", "alerting", "triggered", "alert", "critical", "error", "open", "active")


def alert_history_digest(res: QueryResult, binding: RoleBinding) -> list[str]:
    """How often the alert fired in the lookback, and whether it fires at the same clock time."""
    from omniops.agent.seeds import as_dt, fmt_ts

    ts_col, state_col = binding.columns.get("ts", "ts"), binding.columns.get("state", "state")
    fired = []
    for r in res.as_dicts():
        state = str(r.get(state_col) or "").lower()
        ts = as_dt(r.get(ts_col))
        if ts is not None and (not state or any(w in state for w in _FIRING)) and "normal" not in state:
            fired.append(ts)
    if not fired:
        return ["no firing transition in the lookback"] if res.total_rows else []
    fired.sort()
    hours = Counter(t.strftime("%H") for t in fired)
    hour, n = hours.most_common(1)[0]
    days = len({t.date() for t in fired})
    lines = [f"fired {len(fired)} time(s) on {days} day(s); first {fmt_ts(fired[0])}, last {fmt_ts(fired[-1])}"]
    if days >= 2 and n >= 2:
        lines.append(f"same clock time: {n} of {len(fired)} firings in the {hour}:00 UTC hour (scheduled job?)")
    return lines


def _search_terms(ctx: RunContext) -> tuple[str | None, str | None]:
    """(incident/ticket search term, chat search term): service first, else the alert name."""
    from omniops.agent.seeds import search_text

    a = ctx.alert
    if a is None:
        q = (ctx.question or "")[:120] or None
        return q, q
    return a.service or search_text(ctx) or None, a.rule_name or search_text(ctx) or a.service


async def seed(run: SpecialistRun) -> None:
    from omniops.agent import seeds

    ctx, eng, name = run.ctx, run.engine, run.name
    onset = seeds.onset_time(ctx)
    incident_q, chat_q = _search_terms(ctx)

    def role_seed(role: str, title: str, lookback: timedelta, query: str | None, **kw: Any) -> Any:
        over = {"query": query} if query else {}
        return run.own(title, lambda: seeds.seed_role(ctx, eng, role, title=title, agent=name, vars_over=over,
                                                      where=dict(over), start=onset - lookback, end=onset, **kw))

    await asyncio.gather(
        run.shared("memory", "Memory recall", lambda: seeds.seed_memory(ctx)),
        role_seed("incidents", f"Prior incidents matching '{incident_q}' (30 days)", INCIDENT_LOOKBACK, incident_q,
                  limit=50),
        role_seed("tickets", f"Tickets matching '{incident_q}' (30 days)", INCIDENT_LOOKBACK, incident_q, limit=30,
                  max_bindings=1),
        role_seed("chat_history", f"Chat history mentioning '{chat_q}' (14 days)", CHAT_LOOKBACK, chat_q, limit=50,
                  max_bindings=1),
        role_seed("alert_history", "This alert's firing history (7 days)", ALERT_HISTORY_LOOKBACK, None, limit=500,
                  max_bindings=1, digest=alert_history_digest),
    )


SPEC = SpecialistSpec(
    name="prior_incidents",
    roles=("incidents", "tickets", "docs", "chat_history", "alert_history"),
    prompt="specialist_prior_incidents",
    seed=seed,
    scope_tables=("omni_change_events", "omni_log_patterns"),
    support_roles=("change_events", "code_changes", "logs", "metrics"),   # for the recurrence probe
    max_steps=16,
    available=lambda ctx: ctx.deps.memory is not None,
)
