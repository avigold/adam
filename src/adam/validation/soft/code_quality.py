"""Soft critic: code quality — evaluates readability, maintainability, idiomaticity."""

from __future__ import annotations

import logging

from adam.llm.client import LLMClient
from adam.llm.json_extract import extract_json
from adam.prompts.loader import PromptLoader
from adam.types import ModelTier, ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, register_soft_critic

logger = logging.getLogger(__name__)
_prompts = PromptLoader()


@register_soft_critic("code_quality")
class CodeQualityCritic(BaseValidator):
    """Evaluates code for readability, maintainability, and idiomaticity."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        if not self._llm or not ctx.file_content:
            return ValidationResult(
                validator_name=self.name,
                is_hard=False,
                score=0.5,
                diagnosis="No LLM available or no file content; skipping.",
            )

        prompt = _prompts.render(
            "code_quality.j2",
            language=ctx.file_language or "source",
            file_path=ctx.file_path,
            file_content=ctx.file_content,
            conventions=ctx.conventions,
        )

        resp = await self._llm.complete(
            tier=ModelTier.SONNET,
            messages=[{"role": "user", "content": prompt}],
            system="You are a code quality reviewer. Return only JSON.",
            max_tokens=1000,
            temperature=0.3,
        )

        data = extract_json(resp.text)
        if data is not None:
            avg_score = (
                data.get("readability", 0.5)
                + data.get("maintainability", 0.5)
                + data.get("idiomaticity", 0.5)
            ) / 3
            return ValidationResult(
                validator_name=self.name,
                is_hard=False,
                score=avg_score,
                diagnosis=data.get("diagnosis", ""),
                repair_suggestions=data.get("repair_suggestions", []),
            )
        else:
            e = "No valid JSON found in response"
            logger.warning("Failed to parse code quality response: %s", e)
            return ValidationResult(
                validator_name=self.name,
                is_hard=False,
                score=0.5,
                diagnosis=f"Failed to parse critic response: {e}",
            )
