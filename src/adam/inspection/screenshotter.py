"""Screenshot capture via Playwright headless browser.

Takes screenshots of rendered UI at specified pages/states.
This is a NEW capability that Postwriter doesn't have.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PageSpec:
    """Specification for a page/state to screenshot."""
    url: str
    name: str = ""
    wait_for: str = "networkidle"  # load, domcontentloaded, networkidle
    viewport_width: int = 1280
    viewport_height: int = 720
    actions: list[dict[str, str]] = field(default_factory=list)  # click, type, etc.
    delay_ms: int = 0  # Extra delay after load


@dataclass
class ScreenshotResult:
    """Result of taking a screenshot."""
    page_name: str
    url: str
    image_path: Path
    success: bool
    error: str = ""
    width: int = 0
    height: int = 0


class Screenshotter:
    """Takes screenshots of web pages using Playwright."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or Path(".adam-screenshots")
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, pages: list[PageSpec]) -> list[ScreenshotResult]:
        """Capture screenshots of all specified pages."""
        results: list[ScreenshotResult] = []

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright")
            return [
                ScreenshotResult(
                    page_name=p.name or p.url,
                    url=p.url,
                    image_path=Path(""),
                    success=False,
                    error="Playwright not available",
                )
                for p in pages
            ]

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True)
            except Exception:
                # Chromium not downloaded — install it
                logger.info("Installing Chromium for visual inspection...")
                import subprocess
                subprocess.run(
                    ["playwright", "install", "chromium"],
                    capture_output=True,
                )
                browser = await pw.chromium.launch(headless=True)

            try:
                for page_spec in pages:
                    result = await self._capture_page(browser, page_spec)
                    results.append(result)
            finally:
                await browser.close()

        return results

    async def _capture_page(
        self,
        browser: object,
        spec: PageSpec,
    ) -> ScreenshotResult:
        """Capture a single page screenshot."""
        name = spec.name or spec.url.split("/")[-1] or "index"
        image_path = self._output_dir / f"{_sanitize(name)}.png"

        try:
            # playwright types aren't available at import time
            context = await browser.new_context(  # type: ignore[union-attr]
                viewport={"width": spec.viewport_width, "height": spec.viewport_height},
            )
            page = await context.new_page()

            await page.goto(spec.url, wait_until=spec.wait_for)

            # Execute any specified actions (click, type, etc.)
            for action in spec.actions:
                action_type = action.get("type", "")
                selector = action.get("selector", "")
                value = action.get("value", "")

                if action_type == "click" and selector:
                    await page.click(selector)
                elif action_type == "type" and selector:
                    await page.fill(selector, value)
                elif action_type == "wait":
                    await page.wait_for_timeout(int(value) if value else 1000)

            if spec.delay_ms > 0:
                await page.wait_for_timeout(spec.delay_ms)

            await page.screenshot(path=str(image_path), full_page=True)
            await context.close()

            logger.info("Screenshot captured: %s -> %s", spec.url, image_path)
            return ScreenshotResult(
                page_name=name,
                url=spec.url,
                image_path=image_path,
                success=True,
                width=spec.viewport_width,
                height=spec.viewport_height,
            )

        except Exception as e:
            logger.error("Screenshot failed for %s: %s", spec.url, e)
            return ScreenshotResult(
                page_name=name,
                url=spec.url,
                image_path=image_path,
                success=False,
                error=str(e),
            )

    async def capture_dev_server(
        self,
        server_url: str,
        routes: list[str],
        viewport_width: int = 1280,
        viewport_height: int = 720,
    ) -> list[ScreenshotResult]:
        """Convenience: capture screenshots for a dev server's routes."""
        pages = [
            PageSpec(
                url=f"{server_url.rstrip('/')}{route}",
                name=route.strip("/").replace("/", "_") or "index",
                viewport_width=viewport_width,
                viewport_height=viewport_height,
            )
            for route in routes
        ]
        return await self.capture(pages)


def _sanitize(name: str) -> str:
    """Sanitize a name for use as a filename."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
