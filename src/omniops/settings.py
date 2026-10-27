"""Runtime settings from the environment (.env). Budgets are wall-clock seconds (D25)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    investigation_budget_s: float = 780.0
    report_reserve_s: float = 90.0
    report_call_s: float = 900.0
    specialist_budget_s: float = 240.0
    chat_budget_s: float = 300.0
    watchdog_grace_s: float = 120.0
    deepening_rounds: int = 3
    readiness_pushbacks: int = 3
    depth_pushbacks: int = 2
    evidence_floor: int = 2
    specialists: list[str] = field(default_factory=lambda: ["code_change", "prior_incidents", "customer_impact",
                                                            "dependency", "infrastructure"])
    alert_channels: list[str] = field(default_factory=list)
    flap_window_s: float = 3600.0
    write_mode: str = "stage"            # stage | deny | auto (benchmarks only; auto still records approvals)
    memory_path: str = ".omniops/memory.db"
    case_dir: str = ".omniops/cases"
    post_public: bool = True             # alerts post publicly in the alert thread
    shadow_agents: list[str] = field(default_factory=list)
    benchmark_mode: bool = False
    display_tz: str = "UTC"              # IANA zone for local-time labels shown next to UTC

    @property
    def report_extension_s(self) -> float:
        """How far the report call may run past the investigation budget (D25: the report gets its own,
        much longer budget; the report reserve inside the investigation budget already covers part of it)."""
        return max(0.0, self.report_call_s - self.report_reserve_s)

    @property
    def investigation_guard_s(self) -> float:
        """The outermost wall-clock bound of one investigation (Slack's watchdog, CLI timeouts, drains)."""
        return self.investigation_budget_s + self.report_extension_s + self.watchdog_grace_s

    @property
    def chat_guard_s(self) -> float:
        return self.chat_budget_s + self.watchdog_grace_s

    @classmethod
    def from_env(cls) -> Settings:
        s = cls(
            investigation_budget_s=_f("OMNIOPS_INVESTIGATION_BUDGET_S", 780.0),
            report_reserve_s=_f("OMNIOPS_REPORT_RESERVE_S", 90.0),
            report_call_s=_f("OMNIOPS_REPORT_CALL_S", 900.0),
            specialist_budget_s=_f("OMNIOPS_SPECIALIST_BUDGET_S", 240.0),
            chat_budget_s=_f("OMNIOPS_CHAT_BUDGET_S", 300.0),
            watchdog_grace_s=_f("OMNIOPS_WATCHDOG_GRACE_S", 120.0),
            deepening_rounds=int(_f("OMNIOPS_DEEPENING_ROUNDS", 3)),
            alert_channels=_list("OMNIOPS_ALERT_CHANNELS"),
            write_mode=os.environ.get("OMNIOPS_WRITE_MODE", "stage"),
            memory_path=os.environ.get("OMNIOPS_MEMORY_PATH", ".omniops/memory.db"),
            case_dir=os.environ.get("OMNIOPS_CASE_DIR", ".omniops/cases"),
            shadow_agents=_list("OMNIOPS_SHADOW_AGENTS"),
            benchmark_mode=_b("OMNIOPS_BENCHMARK_MODE", False),
            display_tz=os.environ.get("OMNIOPS_DISPLAY_TZ", "UTC") or "UTC",
        )
        specs = _list("OMNIOPS_SPECIALISTS")
        if specs:
            s.specialists = specs
        return s
