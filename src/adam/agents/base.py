"""Base agent class — all agents inherit from this.

Equivalent to Postwriter's BaseAgent. Handles LLM invocation,
prompt rendering, and response parsing.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from adam.llm.client import LLMClient
from adam.prompts.loader import PromptLoader
from adam.types import AgentContext, AgentResult, ModelTier

logger = logging.getLogger(__name__)

_prompt_loader = PromptLoader()


class BaseAgent:
    """Base class for all Adam agents."""

    role: str = "base"
    model_tier: ModelTier = ModelTier.SONNET
    template_name: str = ""  # e.g. "architect.j2" — if set, used for user message
    response_model: type[BaseModel] | None = None
    use_tool_call: bool = True

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt_loader = _prompt_loader

    async def execute(self, context: AgentContext) -> AgentResult:
        """Run the agent and return a result.

        For structured output agents, retries once with a corrective
        prompt if the first response can't be parsed.
        """
        system_prompt = self.build_system_prompt(context)
        user_message = self.build_user_message(context)

        # Log full prompts at DEBUG for diagnosis
        logger.debug(
            "Agent %s prompt (system): %s", self.role, system_prompt[:500]
        )
        logger.debug(
            "Agent %s prompt (user): %s",
            self.role, user_message[:2000],
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message},
        ]

        tools = None
        tool_choice = None
        if self.response_model and self.use_tool_call:
            schema = self.response_model.model_json_schema()
            tools = [{
                "name": "structured_response",
                "description": f"Return structured {self.role} response",
                "input_schema": schema,
            }]
            tool_choice = {"type": "tool", "name": "structured_response"}

        try:
            resp = await self._llm.complete(
                tier=self.model_tier,
                messages=messages,
                system=system_prompt,
                max_tokens=self._max_tokens(),
                temperature=self._temperature(),
                tools=tools,
                tool_choice=tool_choice,
            )
        except Exception as e:
            logger.error("Agent %s failed: %s", self.role, e)
            return AgentResult(
                success=False,
                agent_role=self.role,
                model_tier=self.model_tier,
                error=str(e),
            )

        # Log response for diagnosis
        logger.debug(
            "Agent %s response (%d in, %d out): %s",
            self.role, resp.input_tokens, resp.output_tokens,
            resp.text[:3000] if resp.text else "(tool_use)",
        )
        logger.info(
            "Agent %s completed: tier=%s in=%d out=%d",
            self.role, self.model_tier.value,
            resp.input_tokens, resp.output_tokens,
        )

        result = self._parse_response(resp, context)

        # Retry once with corrective prompt if structured parsing failed
        if (
            not result.success
            and self.response_model
            and "Parse error" in (result.error or "")
            or "Failed to extract" in (result.error or "")
        ):
            logger.info(
                "Retrying %s with corrective prompt", self.role
            )
            retry_result = await self._retry_with_correction(
                resp.text, messages, system_prompt, tools, tool_choice,
            )
            if retry_result is not None:
                return retry_result

        return result

    async def _retry_with_correction(
        self,
        failed_text: str,
        original_messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
    ) -> AgentResult | None:
        """Retry a failed structured response with corrective guidance."""
        correction = (
            "Your previous response could not be parsed as valid JSON. "
            "Here is what you returned:\n\n"
            f"```\n{failed_text[:2000]}\n```\n\n"
            "Please return ONLY a valid JSON object with no surrounding "
            "text, no markdown fences, and no explanation. Just the JSON."
        )

        messages = original_messages + [
            {"role": "assistant", "content": failed_text},
            {"role": "user", "content": correction},
        ]

        try:
            resp = await self._llm.complete(
                tier=self.model_tier,
                messages=messages,
                system=system_prompt,
                max_tokens=self._max_tokens(),
                temperature=0.3,  # Lower temp for format compliance
                tools=tools,
                tool_choice=tool_choice,
            )
        except Exception as e:
            logger.warning("Retry also failed for %s: %s", self.role, e)
            return None

        result = self._parse_response(resp, AgentContext())
        if result.success:
            logger.info("Retry succeeded for %s", self.role)
        return result if result.success else None

    def build_system_prompt(self, context: AgentContext) -> str:
        """Override to customise system prompt."""
        return f"You are a {self.role} agent in a software engineering system."

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        """Build template variables from AgentContext. Override to customise."""
        return {
            "project_id": context.project_id,
            "project_description": context.project_description,
            "tech_stack": context.tech_stack,
            "architecture": context.architecture,
            "conventions": context.conventions,
            "module_spec": context.module_spec,
            "file_spec": context.file_spec,
            "dependency_interfaces": context.dependency_interfaces,
            "related_files": context.related_files,
            "test_results": context.test_results,
            "error_output": context.error_output,
            "user_context": context.user_context,
            "user_context_images": context.user_context_images,
            "extra": context.extra,
        }

    def build_user_message(self, context: AgentContext) -> str:
        """Build user message. Uses template if template_name is set."""
        if self.template_name and self._prompt_loader.has_template(self.template_name):
            template_ctx = self.build_template_context(context)
            return self._prompt_loader.render(self.template_name, **template_ctx)
        return ""

    def _max_tokens(self) -> int:
        return self._llm.settings.max_response_tokens

    def _temperature(self) -> float:
        return 1.0

    def _parse_response(self, resp: Any, context: AgentContext) -> AgentResult:
        """Parse LLM response into AgentResult.

        For structured responses, tries multiple extraction strategies
        before giving up. Tool_use responses are generally reliable;
        JSON-in-text responses need more robust extraction.
        """
        parsed = None
        total_in = resp.input_tokens
        total_out = resp.output_tokens

        if self.response_model and self.use_tool_call and resp.tool_use:
            tool_input = resp.tool_use[0]["input"]
            try:
                parsed = self.response_model.model_validate(tool_input)
            except Exception as e:
                logger.warning(
                    "Failed to parse tool response for %s: %s",
                    self.role, e,
                )
                return AgentResult(
                    success=False,
                    agent_role=self.role,
                    model_tier=self.model_tier,
                    input_tokens=total_in,
                    output_tokens=total_out,
                    raw_response=resp.text,
                    error=f"Parse error: {e}",
                )

        elif self.response_model and not self.use_tool_call:
            parsed = self._extract_structured(resp.text)

            if parsed is None:
                logger.warning(
                    "JSON extraction failed for %s; raw (first 500): %s",
                    self.role, resp.text[:500],
                )
                logger.debug(
                    "Full failed response for %s: %s",
                    self.role, resp.text,
                )
                return AgentResult(
                    success=False,
                    agent_role=self.role,
                    model_tier=self.model_tier,
                    input_tokens=total_in,
                    output_tokens=total_out,
                    raw_response=resp.text,
                    error="Failed to extract valid JSON from response",
                )

        return AgentResult(
            success=True,
            agent_role=self.role,
            model_tier=self.model_tier,
            input_tokens=total_in,
            output_tokens=total_out,
            raw_response=resp.text,
            parsed=parsed,
        )

    def _extract_structured(self, text: str) -> Any | None:
        """Try multiple strategies to extract structured JSON from text.

        Strategies (in order):
        1. Direct JSON parse of the full text
        2. Extract from ```json ... ``` fences
        3. Extract from ``` ... ``` fences
        4. Find the first { ... } or [ ... ] block
        """
        import json
        import re

        assert self.response_model is not None

        candidates: list[str] = []

        # Strategy 1: raw text
        candidates.append(text.strip())

        # Strategy 2: ```json fenced
        if "```json" in text:
            for block in text.split("```json")[1:]:
                if "```" in block:
                    candidates.append(block.split("```")[0].strip())

        # Strategy 3: ``` fenced (any language)
        if "```" in text:
            parts = text.split("```")
            for i in range(1, len(parts), 2):
                # Skip the language identifier on the first line
                block = parts[i]
                lines = block.split("\n", 1)
                if len(lines) > 1:
                    candidates.append(lines[1].strip())
                candidates.append(block.strip())

        # Strategy 4: find outermost { ... } block
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            candidates.append(brace_match.group(0))

        # Try each candidate
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return self.response_model.model_validate_json(candidate)
            except Exception:
                # Try parsing as dict first, then validating
                try:
                    data = json.loads(candidate)
                    return self.response_model.model_validate(data)
                except Exception:
                    continue

        # Last resort: truncation repair
        from adam.llm.json_extract import repair_truncated_json

        json_start = text.find("{")
        if json_start >= 0:
            repaired = repair_truncated_json(text[json_start:])
            if repaired is not None:
                try:
                    return self.response_model.model_validate(repaired)
                except Exception:
                    pass

        return None
