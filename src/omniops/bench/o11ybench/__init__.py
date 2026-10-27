"""Grafana o11y-bench adapter (Harbor).

- ``harbor_agent``: the Harbor agent class, copied into o11y-bench's ``agents/`` by
  ``scripts/o11ybench_setup.sh``. It never imports omniops (Harbor loads it on the host).
- ``runner``: runs inside the task container: the task is DMed to the bot through the Slack
  simulator, the bot answers over SQL on Grafana/Prometheus/Loki/Tempo HTTP APIs (no MCP).
- ``trajectory``: the ATIF-v1.7 trajectory the o11y-bench grader reads.

See ``docs/BENCH_O11Y.md``.
"""
