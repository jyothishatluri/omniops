"""Infrastructure specialist: are pods, nodes, scheduling, storage, network policy, admission or in-cluster
datastores failing, and exactly why?

Retrospective lessons encoded here:
- 3.2: Kubernetes state (pods not ready / restarting / waiting reasons / last termination) and warning
  events are seeded in code for the alert's namespace; crashlooping containers log their cause before
  dying, so their *previous* container logs are fetched deterministically too.
- Part 5 (CPU pegs): remediation restarts are not causes - restarts and uptime resets are read with
  their timing; a monitoring gap during a peg is a signal, not proof of no coverage.
- 7.1 ("why now?" for capacity failures): OOMs, restarts and pool exhaustion need who/what acted at the
  onset minute (batch jobs, co-tenant writers) - in the playbook prompt (D7).
- Part 5 (CPU pegs): profiles are in scope for pre-onset vs plateau comparisons when a source has them.
- Without Kubernetes, host-resource alerts fall back to the ``hosts`` role.
"""

from __future__ import annotations

from omniops.agent.specialists.base import SpecialistRun, SpecialistSpec


async def seed(run: SpecialistRun) -> None:
    from omniops.agent import seeds

    ctx, eng, name = run.ctx, run.engine, run.name
    state = await run.shared("k8s", "Kubernetes state and warning events", lambda: seeds.seed_k8s(ctx, eng))
    problems = [p for r in state for p in r.data.get("problems", [])]
    if problems:
        await run.own("Previous container logs of restarting pods",
                      lambda: seeds.seed_previous_logs(ctx, eng, problems, agent=name))
    if not ctx.deps.catalog.role_bindings("k8s_state"):
        await run.own("Host inventory and health", lambda: seeds.seed_role(
            ctx, eng, "hosts", title="Hosts / nodes and their health", agent=name, limit=200, max_bindings=1))


SPEC = SpecialistSpec(
    name="infrastructure",
    roles=("k8s_state", "k8s_events", "k8s_logs", "hosts"),
    prompt="specialist_infrastructure",
    seed=seed,
    scope_tables=("omni_log_patterns", "omni_change_events"),
    support_roles=("metrics", "logs", "profiles"),
    max_steps=18,
)
