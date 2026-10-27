"""Customer-impact specialist: who is affected, how badly and since when, on the alert's own signature.

Retrospective lessons encoded here:
- Part 5 ("customer impact measured the wrong thing"): an anomaly affecting 2 sessions was reported as
  site-wide errors because the whole service was measured. The seed is the alert's exact signature
  (its own query over 6 h); unrelated and chronic errors are excluded by comparing with the preceding
  hour in code (new / rising / chronic keys).
- 3.2: the specialist seed fetches the alert's signature by key rather than from list endpoints.
- 4.2: RUM retention and log sampling bias raw counts - the playbook makes counts lower bounds.
- Arena cart-failure: a flag broke one operation while the others kept working - the playbook names
  the operations that still work, so impact is not inflated to the whole service.
- Output keys (``details.level`` / ``details.affected``) feed the conclusion card's impact line.
"""

from __future__ import annotations

import asyncio

from omniops.agent.specialists.base import SpecialistRun, SpecialistSpec


async def seed(run: SpecialistRun) -> None:
    from omniops.agent import seeds

    ctx, eng, name = run.ctx, run.engine, run.name
    svc = (ctx.alert.service if ctx.alert else None) or "the service"
    await asyncio.gather(
        run.shared("alert_query", "The alert's own query over 6h", lambda: seeds.seed_alert_query(ctx, eng)),
        run.shared("log_patterns", "Error log patterns vs the same window a day earlier",
                   lambda: seeds.seed_log_patterns(ctx, eng)),
        run.own("Error-tracking issues vs the preceding hour", lambda: seeds.seed_role_compare(
            ctx, eng, "errors", title=f"Error-tracking issues for '{svc}': window vs the preceding hour", agent=name,
            keys=("title", "message", "resource"))),
        run.own("Real-user errors vs the preceding hour", lambda: seeds.seed_role_compare(
            ctx, eng, "rum", title=f"Real-user errors for '{svc}': window vs the preceding hour", agent=name,
            keys=("resource", "url", "status_code", "message"), vars_over={"error_only": True})),
        run.own("SLO status", lambda: seeds.seed_role(ctx, eng, "slo", title=f"SLOs for '{svc}'", agent=name,
                                                       limit=50, max_bindings=1)),
    )


SPEC = SpecialistSpec(
    name="customer_impact",
    roles=("rum", "errors", "slo", "logs", "log_counts"),
    prompt="specialist_customer_impact",
    seed=seed,
    scope_tables=("omni_log_patterns",),
    support_roles=("metrics", "traces"),
    max_steps=14,
)
