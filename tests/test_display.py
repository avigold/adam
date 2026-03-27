"""Tests for display system — verbs, spinner concurrency."""


from adam.cli.display import _THINKING_VERBS


class TestThinkingVerbs:
    def test_all_present_participle(self):
        """All verbs should be present participles or gerund phrases."""
        for verb in _THINKING_VERBS:
            words = verb.split()
            has_ing = any(w.endswith("ing") for w in words)
            assert has_ing, f"Verb '{verb}' should contain a present participle"

    def test_at_least_15_verbs(self):
        assert len(_THINKING_VERBS) >= 15

    def test_no_duplicates(self):
        assert len(_THINKING_VERBS) == len(set(_THINKING_VERBS))

    def test_engineering_themed(self):
        """Verbs should reference engineering concepts."""
        engineering_keywords = [
            "interface", "type", "stack trace", "module",
            "linter", "test", "depend", "codebase", "contract",
        ]
        all_verbs = " ".join(v.lower() for v in _THINKING_VERBS)
        matches = [kw for kw in engineering_keywords if kw in all_verbs]
        assert len(matches) >= 5, f"Expected engineering themes, got {matches}"

    def test_jewish_themed(self):
        """Verbs should reference Jewish intellectual tradition."""
        jewish_keywords = [
            "sugya", "talmud", "gemara", "pilpul", "tikkun",
            "machloket", "daf", "pshat", "mezuzah", "siyum",
            "davening", "kvelling", "schlepping", "kibbitzing",
        ]
        all_verbs = " ".join(v.lower() for v in _THINKING_VERBS)
        matches = [kw for kw in jewish_keywords if kw in all_verbs]
        assert len(matches) >= 8, f"Expected Jewish themes, got {matches}"
