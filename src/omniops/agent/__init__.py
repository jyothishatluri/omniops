"""OmniOps agent layer: the investigation and chat flows and everything they wire together.

Entry points (imported lazily, PEP 562, so ``import omniops.agent.types`` stays cheap and free of
import cycles with the Slack layer):

- ``investigate(alert, deps, *, status=None, thread=None, run_id=None, anchor=None) -> RunResult``
- ``answer(question, deps, *, thread=None, status=None, anchor=None, run_id=None) -> RunResult``
- ``DefaultRunner(anchor=None)``: the object ``slack.app.build_app`` calls
- ``write_case(result, ctx=None, *, case_dir=None) -> Path | None``: the per-run case file
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "investigate": "omniops.agent.orchestrator",
    "answer": "omniops.agent.chat",
    "DefaultRunner": "omniops.agent.runner",
    "write_case": "omniops.agent.casefile",
}

__all__ = ["DefaultRunner", "answer", "investigate", "write_case"]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'omniops.agent' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
