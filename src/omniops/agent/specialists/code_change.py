"""Code-change correlation specialist: which change preceded the onset, and how does it cause the symptom?

Retrospective lessons encoded here:
- 3.2 / Part 5 (three-flow bake-off): the seed is the org-wide change list from 48 h before to 1 h
  after the trigger; picking repositories by the alerting service's name missed the real release, and
  every flow once missed a deploy three minutes before the alert.
- Part 5 (profile-lookup outage) / 7.1: connect change -> mechanism by reading the diff (the release made an index
  unique); GitHub-like sources are in scope through the ``code_changes`` role.
- 4.2: config, flag, GitOps-sync and image changes count; commit dates are author dates, not deploy
  dates; missing version tags do not mean "no deploy". The playbook prompt carries the method (D7).
"""

from __future__ import annotations

from omniops.agent.specialists.base import SpecialistRun, SpecialistSpec


async def seed(run: SpecialistRun) -> None:
    from omniops.agent import seeds

    await run.shared("changes", "Change events 48h before -> 1h after the trigger (org-wide)",
                     lambda: seeds.seed_changes(run.ctx, run.engine))


SPEC = SpecialistSpec(
    name="code_change",
    roles=("change_events", "code_changes"),
    prompt="specialist_code_change",
    seed=seed,
    scope_tables=("omni_change_events",),
    support_roles=("k8s_state",),        # ReplicaSet revisions, ConfigMaps and flag configs as changes
    max_steps=18,
)
