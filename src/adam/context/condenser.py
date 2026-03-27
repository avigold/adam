"""Context condenser — filters and summarises context files for specific agents.

Equivalent to Postwriter's ContextCondenser.
"""

from __future__ import annotations

from typing import Any

from adam.context.loader import ContextFile
from adam.llm.client import LLMClient
from adam.types import ContextType, ModelTier

MAX_CONDENSED_CHARS = 2000

# Which context types each agent role needs
AGENT_CONTEXT_NEEDS: dict[str, set[ContextType]] = {
    "architect": {ContextType.SPEC, ContextType.ARCHITECTURE, ContextType.TECH_STACK},
    "module_planner": {ContextType.SPEC, ContextType.ARCHITECTURE},
    "file_planner": {ContextType.SPEC, ContextType.STYLE},
    "file_implementer": {ContextType.STYLE, ContextType.REFERENCE},
    "test_writer": {ContextType.SPEC, ContextType.REFERENCE},
    "code_quality": {ContextType.STYLE},
    "security": {ContextType.SPEC},
}


class ContextCondenser:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def filter_for_agent(
        self,
        agent_role: str,
        context_files: list[ContextFile],
    ) -> list[dict[str, Any]]:
        """Return only relevant files for the agent, truncated if needed."""
        needed = AGENT_CONTEXT_NEEDS.get(agent_role, set())
        result = []
        for cf in context_files:
            if cf.context_type not in needed:
                continue
            content = cf.content
            if len(content) > MAX_CONDENSED_CHARS:
                content = content[:MAX_CONDENSED_CHARS] + "\n[truncated]"
            result.append({
                "name": cf.name,
                "type": cf.context_type.value,
                "content": content,
            })
        return result

    async def condense_for_agent(
        self,
        agent_role: str,
        context_files: list[ContextFile],
    ) -> list[dict[str, Any]]:
        """Use Haiku to summarise long context files for a specific agent."""
        cache_key = agent_role
        if cache_key in self._cache:
            return self._cache[cache_key]

        filtered = self.filter_for_agent(agent_role, context_files)

        if self._llm is None:
            return filtered

        condensed = []
        for item in filtered:
            if len(item["content"]) <= MAX_CONDENSED_CHARS:
                condensed.append(item)
                continue

            # Summarise with Haiku
            resp = await self._llm.complete(
                tier=ModelTier.HAIKU,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Summarise this {item['type']} document for a {agent_role} agent "
                        f"working on a software project. Keep all technical details, "
                        f"requirements, and constraints. Be concise.\n\n"
                        f"Document: {item['name']}\n\n{item['content']}"
                    ),
                }],
                max_tokens=1000,
                temperature=0.0,
            )
            condensed.append({
                "name": item["name"],
                "type": item["type"],
                "content": resp.text[:MAX_CONDENSED_CHARS],
            })

        self._cache[cache_key] = condensed
        return condensed
