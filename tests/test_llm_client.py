"""Tests for LLM client — budget tracking and configuration."""

import pytest

from adam.errors import BudgetExhaustedError
from adam.llm.client import LLMClient, TokenBudget
from adam.types import ModelTier


class TestTokenBudget:
    def test_record_usage(self):
        budget = TokenBudget()
        budget.record(ModelTier.SONNET, 100, 50)
        assert budget.usage[ModelTier.SONNET].input_tokens == 100
        assert budget.usage[ModelTier.SONNET].output_tokens == 50
        assert budget.usage[ModelTier.SONNET].calls == 1

    def test_accumulates(self):
        budget = TokenBudget()
        budget.record(ModelTier.SONNET, 100, 50)
        budget.record(ModelTier.SONNET, 200, 100)
        assert budget.usage[ModelTier.SONNET].total_tokens == 450
        assert budget.usage[ModelTier.SONNET].calls == 2

    def test_check_within_budget(self):
        budget = TokenBudget(limits={ModelTier.SONNET: 1000})
        budget.record(ModelTier.SONNET, 100, 50)
        budget.check(ModelTier.SONNET)  # Should not raise

    def test_check_exceeds_budget(self):
        budget = TokenBudget(limits={ModelTier.SONNET: 100})
        budget.record(ModelTier.SONNET, 60, 50)
        with pytest.raises(BudgetExhaustedError):
            budget.check(ModelTier.SONNET)

    def test_unlimited_budget(self):
        budget = TokenBudget()  # No limits set
        budget.record(ModelTier.OPUS, 1_000_000, 500_000)
        budget.check(ModelTier.OPUS)  # Should not raise

    def test_remaining(self):
        budget = TokenBudget(limits={ModelTier.HAIKU: 1000})
        budget.record(ModelTier.HAIKU, 300, 200)
        assert budget.remaining(ModelTier.HAIKU) == 500

    def test_remaining_unlimited(self):
        budget = TokenBudget()
        assert budget.remaining(ModelTier.OPUS) is None

    def test_summary(self):
        budget = TokenBudget(limits={ModelTier.SONNET: 5000})
        budget.record(ModelTier.SONNET, 100, 50)
        s = budget.summary()
        assert s["sonnet"]["input_tokens"] == 100
        assert s["sonnet"]["output_tokens"] == 50
        assert s["sonnet"]["calls"] == 1
        assert s["sonnet"]["remaining"] == 4850


class TestLLMClientConfig:
    def test_default_model_map(self):
        client = LLMClient()
        assert "opus" in client._model_map[ModelTier.OPUS]
        assert "sonnet" in client._model_map[ModelTier.SONNET]
        assert "haiku" in client._model_map[ModelTier.HAIKU]

    def test_budget_limits_from_settings(self):
        from adam.config import LLMSettings
        settings = LLMSettings(sonnet_token_budget=10000)
        client = LLMClient(settings=settings)
        assert client.budget.limits.get(ModelTier.SONNET) == 10000
