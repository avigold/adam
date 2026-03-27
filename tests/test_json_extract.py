"""Tests for robust JSON extraction from LLM responses."""

from adam.llm.json_extract import extract_json


class TestExtractJson:
    def test_clean_json(self):
        result = extract_json('{"score": 0.8, "diagnosis": "good"}')
        assert result is not None
        assert result["score"] == 0.8

    def test_json_in_markdown_fence(self):
        text = 'Here is my analysis:\n```json\n{"score": 0.7}\n```\n'
        result = extract_json(text)
        assert result is not None
        assert result["score"] == 0.7

    def test_json_in_generic_fence(self):
        text = "```\n{\"score\": 0.6}\n```"
        result = extract_json(text)
        assert result is not None
        assert result["score"] == 0.6

    def test_json_with_preamble(self):
        text = "Sure! Here is the result:\n{\"score\": 0.9, \"issues\": []}"
        result = extract_json(text)
        assert result is not None
        assert result["score"] == 0.9

    def test_json_with_trailing_text(self):
        text = '{"score": 0.5}\n\nLet me know if you need anything else!'
        result = extract_json(text)
        assert result is not None
        assert result["score"] == 0.5

    def test_trailing_comma(self):
        text = '{"score": 0.8, "items": ["a", "b",],}'
        result = extract_json(text)
        assert result is not None
        assert result["score"] == 0.8

    def test_nested_json(self):
        text = '{"outer": {"inner": 42}, "list": [1, 2]}'
        result = extract_json(text)
        assert result is not None
        assert result["outer"]["inner"] == 42

    def test_no_json(self):
        result = extract_json("This is just plain text with no JSON.")
        assert result is None

    def test_empty_string(self):
        result = extract_json("")
        assert result is None

    def test_array_not_object(self):
        """We want dicts, not arrays."""
        result = extract_json("[1, 2, 3]")
        assert result is None

    def test_multiple_fenced_blocks(self):
        text = (
            "First attempt:\n```json\n{invalid}\n```\n"
            "Corrected:\n```json\n{\"score\": 0.75}\n```"
        )
        result = extract_json(text)
        assert result is not None
        assert result["score"] == 0.75

    def test_json_with_language_tag(self):
        text = "```javascript\n{\"framework\": \"react\"}\n```"
        result = extract_json(text)
        assert result is not None
        assert result["framework"] == "react"
