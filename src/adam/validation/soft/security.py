"""Soft critic: security — checks for common vulnerabilities."""

from __future__ import annotations

import logging

from adam.llm.client import LLMClient
from adam.llm.json_extract import extract_json
from adam.prompts.loader import PromptLoader
from adam.types import ModelTier, ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, register_soft_critic

logger = logging.getLogger(__name__)
_prompts = PromptLoader()


@register_soft_critic("security")
class SecurityCritic(BaseValidator):
    """Reviews code for security vulnerabilities."""

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
            "security.j2",
            file_path=ctx.file_path,
            file_content=ctx.file_content,
            file_type=ctx.file_type,
        )

        resp = await self._llm.complete(
            tier=ModelTier.SONNET,
            messages=[{"role": "user", "content": prompt}],
            system=(
                "You are a security auditor. Report real issues, "
                "not theoretical ones. Return only JSON."
            ),
            max_tokens=1500,
            temperature=0.2,
        )

        data = extract_json(resp.text)
        if data is not None:
            return ValidationResult(
                validator_name=self.name,
                is_hard=False,
                score=data.get("score", 0.5),
                diagnosis=data.get("diagnosis", ""),
                evidence=data.get("vulnerabilities", []),
                repair_suggestions=data.get("repair_suggestions", []),
            )
        else:
            e = "No valid JSON found in response"
            logger.warning("Failed to parse security response: %s", e)
            return ValidationResult(
                validator_name=self.name,
                is_hard=False,
                score=0.5,
                diagnosis=f"Failed to parse critic response: {e}",
            )
