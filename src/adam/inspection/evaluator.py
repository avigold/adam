"""Visual evaluator — sends screenshots to Opus with vision for evaluation.

Uses Claude's vision capabilities to evaluate rendered UI against spec.
This is the "viewing the result" described in CLAUDE.md Section 14.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from adam.inspection.screenshotter import ScreenshotResult
from adam.llm.client import LLMClient
from adam.types import ModelTier

logger = logging.getLogger(__name__)


@dataclass
class VisualIssue:
    """A single visual issue found in a screenshot."""
    severity: str  # critical, major, minor
    category: str  # layout, styling, missing_element, broken, accessibility
    description: str
    suggestion: str = ""


@dataclass
class VisualEvaluation:
    """Result of visual evaluation for a single page."""
    page_name: str
    score: float  # 0.0-1.0
    issues: list[VisualIssue] = field(default_factory=list)
    summary: str = ""
    passes: bool = True


class VisualEvaluator:
    """Evaluates screenshots against specification using Opus vision."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def evaluate(
        self,
        screenshots: list[ScreenshotResult],
        spec_description: str = "",
        page_specs: dict[str, str] | None = None,
    ) -> list[VisualEvaluation]:
        """Evaluate all captured screenshots."""
        results: list[VisualEvaluation] = []

        for ss in screenshots:
            if not ss.success:
                results.append(VisualEvaluation(
                    page_name=ss.page_name,
                    score=0.0,
                    summary=f"Screenshot failed: {ss.error}",
                    passes=False,
                ))
                continue

            page_description = ""
            if page_specs:
                page_description = page_specs.get(ss.page_name, "")

            evaluation = await self._evaluate_screenshot(
                ss, spec_description, page_description,
            )
            results.append(evaluation)

        return results

    async def _evaluate_screenshot(
        self,
        screenshot: ScreenshotResult,
        spec_description: str,
        page_description: str,
    ) -> VisualEvaluation:
        """Evaluate a single screenshot with Opus vision."""
        if not screenshot.image_path.exists():
            return VisualEvaluation(
                page_name=screenshot.page_name,
                score=0.0,
                summary="Screenshot file not found",
                passes=False,
            )

        # Read and encode image
        image_data = screenshot.image_path.read_bytes()
        b64_image = base64.standard_b64encode(image_data).decode("utf-8")

        # Determine media type
        suffix = screenshot.image_path.suffix.lower()
        media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        media_type = media_types.get(suffix, "image/png")

        # Build evaluation prompt
        prompt_parts = [
            "Evaluate this screenshot of a web application.",
        ]
        if spec_description:
            prompt_parts.append(
                f"\nProject specification:\n{spec_description}"
            )
        if page_description:
            prompt_parts.append(
                f"\nThis page should show:\n{page_description}"
            )
        prompt_parts.append(
            "\nEvaluate for:"
            "\n1. Layout correctness (elements properly positioned)"
            "\n2. Visual completeness (all expected elements present)"
            "\n3. Styling (colors, fonts, spacing look intentional)"
            "\n4. Responsiveness (no overflow, no cut-off content)"
            "\n5. Accessibility (contrast, readable text sizes)"
            "\n6. Obvious bugs (broken images, overlapping elements, "
            "empty states)"
            "\n\nRespond with JSON:"
            '\n{"score": 0.0-1.0, "summary": "...", "issues": '
            '[{"severity": "critical|major|minor", '
            '"category": "layout|styling|missing_element|broken|'
            'accessibility", "description": "...", "suggestion": "..."}]}'
        )

        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_image,
                    },
                },
                {
                    "type": "text",
                    "text": "\n".join(prompt_parts),
                },
            ],
        }]

        try:
            resp = await self._llm.complete(
                tier=ModelTier.OPUS,
                messages=messages,
                system=(
                    "You are a UI/UX evaluator. Examine the screenshot "
                    "and provide honest, specific feedback. Return only "
                    "JSON."
                ),
                max_tokens=2000,
                temperature=0.3,
            )

            data = json.loads(resp.text)
            issues = [
                VisualIssue(
                    severity=i.get("severity", "minor"),
                    category=i.get("category", "styling"),
                    description=i.get("description", ""),
                    suggestion=i.get("suggestion", ""),
                )
                for i in data.get("issues", [])
            ]

            score = float(data.get("score", 0.5))
            has_critical = any(i.severity == "critical" for i in issues)

            return VisualEvaluation(
                page_name=screenshot.page_name,
                score=score,
                issues=issues,
                summary=data.get("summary", ""),
                passes=score >= 0.5 and not has_critical,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Failed to parse visual evaluation for %s: %s",
                screenshot.page_name, e,
            )
            return VisualEvaluation(
                page_name=screenshot.page_name,
                score=0.5,
                summary=f"Evaluation parse failed: {e}",
                passes=True,  # Don't block on parse failures
            )
