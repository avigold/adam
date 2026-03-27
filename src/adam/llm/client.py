"""LLM client with model tiering, rate limiting, and token budgets."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from adam.config import LLMSettings
from adam.errors import BudgetExhaustedError
from adam.types import ModelTier

logger = logging.getLogger(__name__)

_ADAM_CONFIG_DIR = Path.home() / ".adam"
_ADAM_CONFIG_FILE = _ADAM_CONFIG_DIR / "config"


def _resolve_api_key() -> str:
    """Resolve Anthropic API key from environment or ~/.adam/config.

    Check order:
    1. ANTHROPIC_API_KEY env var
    2. ~/.adam/config file
    3. Prompt user and save to ~/.adam/config
    """
    import os

    # 1. Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key

    # 2. Config file
    if _ADAM_CONFIG_FILE.exists():
        for line in _ADAM_CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key

    # 3. Prompt user (only if running interactively)
    import sys
    if not sys.stdin.isatty():
        return ""

    try:
        from rich.prompt import Prompt
        key = Prompt.ask(
            "[bold]Anthropic API key[/bold] "
            "[dim](from console.anthropic.com, saved to ~/.adam/config)[/dim]"
        )
    except (ImportError, EOFError):
        return ""

    if key:
        _ADAM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Append to config (don't overwrite other settings)
        with open(_ADAM_CONFIG_FILE, "a") as f:
            f.write(f"ANTHROPIC_API_KEY={key}\n")
        logger.info("API key saved to %s", _ADAM_CONFIG_FILE)

    return key


# ---------------------------------------------------------------------------
# Response wrapper
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    text: str = ""
    tool_use: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    stop_reason: str = ""


# ---------------------------------------------------------------------------
# Token budget tracking
# ---------------------------------------------------------------------------

@dataclass
class TierUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenBudget:
    limits: dict[ModelTier, int] = field(default_factory=dict)
    usage: dict[ModelTier, TierUsage] = field(default_factory=lambda: {
        ModelTier.OPUS: TierUsage(),
        ModelTier.SONNET: TierUsage(),
        ModelTier.HAIKU: TierUsage(),
    })

    def record(self, tier: ModelTier, input_tokens: int, output_tokens: int) -> None:
        u = self.usage[tier]
        u.input_tokens += input_tokens
        u.output_tokens += output_tokens
        u.calls += 1

    def check(self, tier: ModelTier) -> None:
        limit = self.limits.get(tier, 0)
        if limit > 0 and self.usage[tier].total_tokens >= limit:
            raise BudgetExhaustedError(
                f"{tier.value} budget exhausted: {self.usage[tier].total_tokens}/{limit}"
            )

    def remaining(self, tier: ModelTier) -> int | None:
        limit = self.limits.get(tier, 0)
        if limit == 0:
            return None
        return max(0, limit - self.usage[tier].total_tokens)

    def summary(self) -> dict[str, dict[str, int | None]]:
        return {
            tier.value: {
                "input_tokens": self.usage[tier].input_tokens,
                "output_tokens": self.usage[tier].output_tokens,
                "calls": self.usage[tier].calls,
                "remaining": self.remaining(tier),
            }
            for tier in ModelTier
        }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(
        self,
        settings: LLMSettings | None = None,
        budget: TokenBudget | None = None,
    ) -> None:
        self.settings = settings or LLMSettings()
        self.budget = budget or TokenBudget()
        api_key = self.settings.anthropic_api_key or _resolve_api_key()
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or None
        )

        self._model_map: dict[ModelTier, str] = {
            ModelTier.OPUS: self.settings.opus_model,
            ModelTier.SONNET: self.settings.sonnet_model,
            ModelTier.HAIKU: self.settings.haiku_model,
        }

        self._semaphores: dict[ModelTier, asyncio.Semaphore] = {
            ModelTier.OPUS: asyncio.Semaphore(self.settings.max_concurrent_opus),
            ModelTier.SONNET: asyncio.Semaphore(self.settings.max_concurrent_sonnet),
            ModelTier.HAIKU: asyncio.Semaphore(self.settings.max_concurrent_haiku),
        }

        # Wire up budget limits from settings
        for tier, attr in [
            (ModelTier.OPUS, "opus_token_budget"),
            (ModelTier.SONNET, "sonnet_token_budget"),
            (ModelTier.HAIKU, "haiku_token_budget"),
        ]:
            val = getattr(self.settings, attr, 0)
            if val > 0:
                self.budget.limits[tier] = val

    async def complete(
        self,
        tier: ModelTier,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.budget.check(tier)

        model = self._model_map[tier]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        async with self._semaphores[tier]:
            response = await self._call_with_retry(kwargs)

        # Parse response
        result = LLMResponse(
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason or "",
        )

        for block in response.content:
            if block.type == "text":
                result.text += block.text
            elif block.type == "tool_use":
                result.tool_use.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        self.budget.record(tier, result.input_tokens, result.output_tokens)
        logger.debug(
            "LLM call: tier=%s model=%s in=%d out=%d",
            tier.value, model, result.input_tokens, result.output_tokens,
        )
        return result

    async def _call_with_retry(
        self,
        kwargs: dict[str, Any],
        max_retries: int = 3,
    ) -> Any:
        for attempt in range(max_retries):
            try:
                # Use streaming to avoid 10-minute timeout on large responses
                async with self._client.messages.stream(**kwargs) as stream:
                    return await stream.get_final_message()
            except anthropic.RateLimitError:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Rate limited, retrying in %ds", wait)
                    await asyncio.sleep(wait)
                else:
                    raise
            except anthropic.APITimeoutError:
                if attempt < max_retries - 1:
                    logger.warning("Timeout, retrying (attempt %d)", attempt + 1)
                    await asyncio.sleep(1)
                else:
                    raise
