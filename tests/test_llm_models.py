"""Model configuration from the environment, role defaults, pricing and the make_llm entry point."""

from __future__ import annotations

from typing import get_args

import pytest

from omniops.llm import (
    ROLE_DEFAULTS,
    ChatCompletionsClient,
    MetaResponsesClient,
    ModelConfig,
    Usage,
    cost,
    make_llm,
)
from omniops.llm.base import Role
from omniops.llm.models import http_timeout_for, normalize_effort
from omniops.settings import Settings

ENV = ["OMNIOPS_LLM_BASE_URL", "OMNIOPS_LLM_API_KEY_ENV", "OMNIOPS_LLM_PROTOCOL", "OMNIOPS_MODEL_HEAVY",
       "OMNIOPS_MODEL_MID", "OMNIOPS_MODEL_FAST", "OMNIOPS_ALLOW_CONTRIBUTOR_MODELS", "MODEL_API_KEY"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults_target_the_meta_model_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "k-123")
    cfg = ModelConfig.from_env()
    assert cfg.base_url == "https://api.meta.ai/v1" and cfg.protocol == "responses" and cfg.api_key == "k-123"
    assert cfg.models == {"heavy": "muse-wonder-1.0", "mid": "muse-spark-1.3", "fast": "muse-spark-1.3"}
    assert "k-123" not in repr(cfg)                                          # secrets never reach logs
    assert cfg.ladder("specialist") == ["muse-spark-1.3", "muse-wonder-1.0"]
    assert cfg.ladder("report") == ["muse-wonder-1.0"]
    assert isinstance(make_llm(config=cfg), MetaResponsesClient)


def test_env_overrides_and_chat_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIOPS_LLM_BASE_URL", "https://gateway.example/v1/")
    monkeypatch.setenv("OMNIOPS_LLM_API_KEY_ENV", "GATEWAY_TOKEN")
    monkeypatch.setenv("GATEWAY_TOKEN", "g-1")
    monkeypatch.setenv("OMNIOPS_LLM_PROTOCOL", "chat")
    monkeypatch.setenv("OMNIOPS_MODEL_HEAVY", "big")
    monkeypatch.setenv("OMNIOPS_MODEL_FAST", "tiny")
    cfg = ModelConfig.from_env()
    assert cfg.base_url == "https://gateway.example/v1" and cfg.api_key == "g-1" and cfg.api_key_env == "GATEWAY_TOKEN"
    assert cfg.models == {"heavy": "big", "mid": "muse-spark-1.3", "fast": "tiny"}
    llm = make_llm(Settings(report_call_s=600))
    assert isinstance(llm, ChatCompletionsClient)
    assert llm.config.role("report").timeout_s == 600 and ROLE_DEFAULTS["report"].timeout_s == 900

    monkeypatch.setenv("OMNIOPS_LLM_PROTOCOL", "grpc")
    with pytest.raises(ValueError, match="responses' or 'chat'"):
        ModelConfig.from_env()


def test_contributor_models_are_refused_unless_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIOPS_MODEL_MID", "muse-spark-1.3-contributor")
    with pytest.raises(ValueError, match="train on prompts"):
        ModelConfig.from_env()
    monkeypatch.setenv("OMNIOPS_ALLOW_CONTRIBUTOR_MODELS", "1")
    assert ModelConfig.from_env().models["mid"] == "muse-spark-1.3-contributor"


def test_every_role_has_sized_defaults_and_nested_timeouts() -> None:
    assert set(ROLE_DEFAULTS) == set(get_args(Role))
    heavy_roles = {"decision", "evidence", "hypotheses", "verdict", "deepening", "report", "chat", "judge"}
    assert {r for r, d in ROLE_DEFAULTS.items() if d.tier == "heavy"} == heavy_roles   # comprehension on top tier
    assert ROLE_DEFAULTS["report"].max_output_tokens == 64_000
    assert ROLE_DEFAULTS["verdict"].max_output_tokens == ROLE_DEFAULTS["formatter"].max_output_tokens == 8_000
    assert ROLE_DEFAULTS["specialist"].escalate_to == "heavy"
    for d in ROLE_DEFAULTS.values():
        assert http_timeout_for(d.timeout_s) > d.timeout_s
        assert d.effort != "none"


def test_effort_normalisation_and_cost() -> None:
    assert normalize_effort("none", "responses") == "minimal"               # Muse rejects "none"
    assert normalize_effort("max", "responses") == "max"
    assert normalize_effort("max", "chat") == "xhigh"
    assert normalize_effort("turbo", "responses") is None
    usage = Usage(input_tokens=1_000_000, cached_tokens=600_000, output_tokens=100_000, reasoning_tokens=80_000)
    assert cost("muse-wonder-1.0", usage) == pytest.approx(400_000 * 4.0 / 1e6 + 600_000 * 0.2 / 1e6 + 2.0)
    assert cost("muse-spark-1.3", usage) == pytest.approx(0.5 + 0.09 + 0.425)
    assert cost("unpriced-model", usage) == 0.0
