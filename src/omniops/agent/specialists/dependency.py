"""Dependency specialist: is the alerting service a victim of an upstream, downstream or shared dependency?

Retrospective lessons encoded here:
- Part 5 (Content API cold cache): another service's nightly job deleted keys on a shared cache; the
  database took 10-25x load and pods hit their memory limit. The method: co-tenant writes on shared
  datastores, a mandatory recurrence probe, and comparison against the preceding hour (seeded in code).
- 3.2: the dependency seed is the service map in both directions.
- 3.6.1: "A floods / invalidates B" needs evidence from B's side; otherwise confidence stays <= 0.5.
- 3.12.5: evidence-driven dependency drill-down only - the playbook does not chase every dependency.
- D8: this agent's first version ran in shadow mode; shadow runs stay private (S ids).
"""

from __future__ import annotations

import asyncio

from omniops.agent.specialists.base import SpecialistRun, SpecialistSpec


async def seed(run: SpecialistRun) -> None:
    from omniops.agent import seeds

    ctx, eng, name = run.ctx, run.engine, run.name
    service = ctx.alert.service if ctx.alert else None
    label = service or "the service"
    await asyncio.gather(
        run.own("Service dependency map (both directions)", lambda: seeds.seed_role(
            ctx, eng, "service_map", title=f"Service dependency map around '{label}' (both directions)", agent=name,
            limit=300, digest=seeds.service_map_digest(service))),
        run.own("Error spans vs the preceding hour", lambda: seeds.seed_role_compare(
            ctx, eng, "traces", title=f"Error spans of '{label}' by service/operation: window vs the preceding hour",
            agent=name, keys=("service", "resource", "status_code"), vars_over={"error_only": True})),
        run.shared("active_alerts", "Other active alerts", lambda: seeds.seed_active_alerts(ctx, eng)),
    )


SPEC = SpecialistSpec(
    name="dependency",
    roles=("service_map", "traces"),
    prompt="specialist_dependency",
    seed=seed,
    scope_tables=("omni_log_patterns", "omni_active_alerts", "omni_change_events"),
    support_roles=("trace_detail", "metrics", "logs"),
    max_steps=16,
)
