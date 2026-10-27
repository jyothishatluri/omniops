"""Edge Delta AI SRE Arena adapter: the Arena's Kubernetes incidents, investigated through Slack.

Flow per scenario (``runner``): reset to baseline -> inject the fault (kubectl or a GitOps commit) ->
verify it manifests -> wait (bounded) for Grafana alerts -> post the launch message into Slack (a
Grafana Alerting notification, or a support-desk customer report built only from user-visible
symptoms: ``launch``) -> OmniOps investigates over SQL (Kubernetes, Argo CD, GitHub, Grafana stack)
-> the delivered Slack answer is archived verbatim (``records``) -> Arena packet + judge -> reset.

Lessons encoded (OpsIntelligence retrospective): D1 (Slack is the only entry point), 3.16 (ground
truth stays judge-only and never reaches the launch text), 3.18 (judge sees the real tool activity;
smoke runs pin the production flow), 4.1 (loud zeros), D28 / Part 8 #34 (humans approve publishes).

Infrastructure lives in ``deploy/arena`` and the operator guide in ``docs/BENCH_ARENA.md``.
"""
