"""Tests for generation profiles."""

from adam.config import LLMSettings, OrchestratorSettings
from adam.profiles import PROFILES, apply_profile, list_profiles


class TestProfiles:
    def test_all_profiles_exist(self):
        assert "fast_draft" in PROFILES
        assert "standard" in PROFILES
        assert "high_quality" in PROFILES
        assert "budget_conscious" in PROFILES

    def test_apply_fast_draft(self):
        settings = OrchestratorSettings()
        apply_profile("fast_draft", settings)
        assert settings.max_repair_rounds == 1
        assert settings.run_soft_critics is False

    def test_apply_high_quality(self):
        settings = OrchestratorSettings()
        apply_profile("high_quality", settings)
        assert settings.max_repair_rounds == 5
        assert settings.acceptance_threshold == 0.7
        assert settings.run_soft_critics is True
        assert settings.visual_inspection is True

    def test_apply_budget_conscious_llm(self):
        orch = OrchestratorSettings()
        llm = LLMSettings()
        apply_profile("budget_conscious", orch, llm)
        assert llm.sonnet_token_budget == 500_000
        assert llm.opus_token_budget == 50_000

    def test_apply_unknown_profile(self):
        settings = OrchestratorSettings()
        original_rounds = settings.max_repair_rounds
        apply_profile("nonexistent", settings)
        assert settings.max_repair_rounds == original_rounds

    def test_list_profiles(self):
        profiles = list_profiles()
        assert len(profiles) >= 4
        names = {p["name"] for p in profiles}
        assert "fast_draft" in names
        assert all("description" in p for p in profiles)
