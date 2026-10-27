"""LLM layer: provider-neutral interface, Meta Model API clients, schema-bound output, test double.

``make_llm()`` builds the process-wide client from the environment (see ``ModelConfig.from_env``);
agent code takes a per-run view with ``llm.for_run(run_id)`` so escalation stays sticky within a run
and usage is accounted per run (3.13, 3.9.1).
"""

from __future__ import annotations

import dataclasses
import logging

from omniops.llm.base import (
    LLMClient,
    LLMResponse,
    LoopEvent,
    LoopResult,
    Role,
    ToolCall,
    ToolOutput,
    ToolSpec,
    Usage,
)
from omniops.llm.meta import ChatCompletionsClient, LLMError, LLMTimeout, MetaResponsesClient, RunScopedLLM
from omniops.llm.models import PRICING, ROLE_DEFAULTS, ModelConfig, RoleDefaults, cost
from omniops.llm.scripted import ScriptedCall, ScriptedLLM
from omniops.llm.structured import coerce_and_validate, normalize_label, parse_json_lenient, to_json_schema
from omniops.settings import Settings

log = logging.getLogger("omniops.llm")

__all__ = [
    "PRICING", "ROLE_DEFAULTS", "ChatCompletionsClient", "LLMClient", "LLMError", "LLMResponse", "LLMTimeout",
    "LoopEvent", "LoopResult", "MetaResponsesClient", "ModelConfig", "Role", "RoleDefaults", "RunScopedLLM",
    "ScriptedCall", "ScriptedLLM", "ToolCall", "ToolOutput", "ToolSpec", "Usage", "coerce_and_validate", "cost",
    "make_llm", "normalize_label", "parse_json_lenient", "to_json_schema",
]


def make_llm(settings: Settings | None = None, *,
             config: ModelConfig | None = None) -> MetaResponsesClient | ChatCompletionsClient:
    """The configured client. ``OMNIOPS_LLM_PROTOCOL=chat`` selects Chat Completions.

    ``settings.report_call_s`` becomes the report role's outer wait (the HTTP timeout stays above it).
    """
    cfg = config or ModelConfig.from_env()
    if settings is not None:
        cfg.roles["report"] = dataclasses.replace(cfg.role("report"), timeout_s=settings.report_call_s)
    if not cfg.api_key:
        log.warning("%s is not set: every model call will fail until it is", cfg.api_key_env)
    return ChatCompletionsClient(cfg) if cfg.protocol == "chat" else MetaResponsesClient(cfg)
