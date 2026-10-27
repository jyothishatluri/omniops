"""Role -> model configuration, per-call budgets, escalation ladders and pricing.

Retrospective lessons encoded here:
- D13 / 3.13: tier by comprehension, not cost. Every evidence, hypothesis, verdict, decision and
  report role runs on the heavy model; only validated-with-fallback roles (formatter, triage, alert
  parsing, memory extraction) and the specialists' loops use the mid/fast tier, the latter behind a
  sticky escalation ladder.
- D14: effort is the reliable cost/latency lever (medium for loops and reports, high for verdicts and
  hypotheses) - "think less" in a prompt is not.
- D15 / 3.8.5: output ceilings are correctness constraints. ``max_output_tokens`` covers reasoning +
  visible output, so it is sized per role (report 64k, verdict/formatter 8k) with a probed ceiling
  for one retry.
- 3.13: timeouts nest correctly. ``timeout_s`` is the outer wait for one logical call (about 2x the
  slowest healthy call); the HTTP timeout is always larger (``http_timeout_for``) so the outer wait
  fires first and nothing is silently re-sent and paid for twice.
- 3.9.1: budgets are honest about tokens and money - pricing per model, cached tokens billed at the
  cached rate, a per-role input budget that is checked against the provider's reported usage.
- Data policy: ``*-contributor`` models let Meta train on prompts; ops data must not leave under that
  policy, so they are refused unless ``OMNIOPS_ALLOW_CONTRIBUTOR_MODELS=1``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, get_args

from omniops.llm.base import Role, Usage

log = logging.getLogger("omniops.llm")

Tier = Literal["heavy", "mid", "fast"]
Protocol = Literal["responses", "chat"]
Effort = Literal["minimal", "low", "medium", "high", "xhigh", "max"]

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODELS: dict[Tier, str] = {"heavy": "muse-wonder-1.0", "mid": "muse-spark-1.3", "fast": "muse-spark-1.3"}
EFFORTS: tuple[str, ...] = get_args(Effort)
CHAT_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")   # other OpenAI-compatible endpoints lack "max"
HTTP_TIMEOUT_MARGIN_S = 30.0
OUTPUT_CEILING = 128_000      # the most one call may ask for (one retry after a max_output_tokens stop)


def http_timeout_for(outer_wait_s: float) -> float:
    """HTTP read timeout for a request whose caller waits ``outer_wait_s``: always larger (3.13)."""
    return max(outer_wait_s, 1.0) + HTTP_TIMEOUT_MARGIN_S


@dataclass(frozen=True)
class RoleDefaults:
    tier: Tier
    effort: Effort
    max_output_tokens: int        # reasoning + visible output (D15, 3.8.5)
    timeout_s: float              # outer wait for one call; HTTP timeout = http_timeout_for(timeout_s)
    input_budget_tokens: int      # warn when a call's reported input exceeds it (3.9.1)
    escalate_to: Tier | None = None


ROLE_DEFAULTS: dict[Role, RoleDefaults] = {
    # Comprehension-heavy roles never go below the top tier (D13).
    "decision": RoleDefaults("heavy", "medium", 16_000, 150, 50_000),
    "evidence": RoleDefaults("heavy", "medium", 8_000, 90, 40_000),
    "hypotheses": RoleDefaults("heavy", "high", 16_000, 120, 50_000),
    "verdict": RoleDefaults("heavy", "high", 8_000, 90, 30_000),
    "deepening": RoleDefaults("heavy", "medium", 12_000, 90, 40_000),
    "report": RoleDefaults("heavy", "medium", 64_000, 900, 60_000),
    "chat": RoleDefaults("heavy", "medium", 32_000, 150, 50_000),
    "judge": RoleDefaults("heavy", "high", 8_000, 120, 60_000),
    # Mid tier behind a sticky ladder to the heavy model (D14, 3.13).
    "specialist": RoleDefaults("mid", "medium", 12_000, 120, 40_000, escalate_to="heavy"),
    # Validated output with a deterministic fallback: cheaper tiers are safe here (3.13).
    "formatter": RoleDefaults("fast", "low", 8_000, 90, 30_000, escalate_to="mid"),
    "triage": RoleDefaults("fast", "low", 2_000, 30, 8_000, escalate_to="mid"),
    "alert_parse": RoleDefaults("fast", "low", 4_000, 45, 8_000, escalate_to="mid"),
    "memory": RoleDefaults("fast", "low", 4_000, 60, 20_000, escalate_to="mid"),
}

# USD per 1M tokens: (input, cached input, output). Reasoning tokens are billed as output.
PRICING: dict[str, tuple[float, float, float]] = {
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.1": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),
}
_unpriced_warned: set[str] = set()


def cost(model: str, usage: Usage) -> float:
    """USD cost of one call. ``cached_tokens`` is a subset of ``input_tokens``; reasoning is output."""
    price = PRICING.get(model)
    if price is None:
        if model not in _unpriced_warned:            # loud zero: a cost of 0 must not look real
            _unpriced_warned.add(model)
            log.warning("no pricing for model %s: its cost is reported as 0", model)
        return 0.0
    p_in, p_cached, p_out = price
    cached = min(usage.cached_tokens, usage.input_tokens)
    return ((usage.input_tokens - cached) * p_in + cached * p_cached + usage.output_tokens * p_out) / 1e6


def is_contributor(model: str) -> bool:
    return "contributor" in model.lower()


def normalize_effort(effort: str | None, protocol: Protocol) -> str | None:
    """Map an effort to one the endpoint accepts: Muse rejects "none"; generic chat endpoints may lack "max"."""
    if effort is None:
        return None
    e = effort.strip().lower()
    if e == "none":
        e = "minimal"
    if e not in EFFORTS:
        log.warning("unknown reasoning effort %r: letting the model choose", effort)
        return None
    if protocol == "chat" and e not in CHAT_EFFORTS:
        e = "xhigh"
    return e


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ModelConfig:
    """Everything the LLM clients need. The API key is read from the env var named ``api_key_env``."""

    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = "MODEL_API_KEY"
    protocol: Protocol = "responses"
    models: dict[Tier, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    roles: dict[Role, RoleDefaults] = field(default_factory=lambda: dict(ROLE_DEFAULTS))
    allow_contributor: bool = False
    cache_retention: str | None = "24h"   # Responses API only; reused across investigations (3.14)
    max_http_attempts: int = 4            # per request, for 429/5xx/529 and transport errors
    retry_base_s: float = 0.5
    retry_jitter_s: float = 1.0
    retry_max_delay_s: float = 60.0
    escalate_after: int = 3               # one try + two retries on a model, then the next tier (3.13)
    output_ceiling: int = OUTPUT_CEILING
    # Long generations stream (Responses API): a non-streamed call is cut by the provider's time limit
    # (HTTP 504). Once output has started, a stream silent for ``stream_stall_s`` is retried once (3.13).
    stream_roles: tuple[str, ...] = ("report",)
    stream_stall_s: float = 120.0
    api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.protocol not in ("responses", "chat"):
            raise ValueError(f"OMNIOPS_LLM_PROTOCOL must be 'responses' or 'chat', not {self.protocol!r}")
        bad = sorted({m for m in self.models.values() if is_contributor(m)})
        if bad and not self.allow_contributor:
            raise ValueError(
                f"contributor models {bad} allow the provider to train on prompts; ops data must not be sent "
                "under that policy. Set OMNIOPS_ALLOW_CONTRIBUTOR_MODELS=1 only for non-production data.")

    @classmethod
    def from_env(cls) -> ModelConfig:
        key_env = os.environ.get("OMNIOPS_LLM_API_KEY_ENV", "MODEL_API_KEY")
        models = dict(DEFAULT_MODELS)
        for tier in models:
            override = os.environ.get(f"OMNIOPS_MODEL_{tier.upper()}", "").strip()
            if override:
                models[tier] = override
        return cls(
            base_url=os.environ.get("OMNIOPS_LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key_env=key_env,
            protocol=os.environ.get("OMNIOPS_LLM_PROTOCOL", "responses").strip().lower(),  # type: ignore[arg-type]
            models=models,
            allow_contributor=_bool_env("OMNIOPS_ALLOW_CONTRIBUTOR_MODELS"),
            api_key=os.environ.get(key_env, ""),
        )

    def role(self, role: Role) -> RoleDefaults:
        return self.roles.get(role) or ROLE_DEFAULTS["decision"]

    def ladder(self, role: Role) -> list[str]:
        """Model names to try for a role, in escalation order (duplicates collapse)."""
        rd = self.role(role)
        out = [self.models[rd.tier]]
        if rd.escalate_to and self.models[rd.escalate_to] not in out:
            out.append(self.models[rd.escalate_to])
        return out
