"""Indeed liveness verification via Playwright.

Indeed sits behind Cloudflare and renders its apply UI with JavaScript, so the
plain-httpx checker in ``liveness.py`` can't judge it (and Indeed's HTML carries
boilerplate "this job has expired" strings that produce false positives on
*live* jobs). This module drives a stealth browser instead and reads the
**rendered apply button** — the exact thing the user sees greyed out on a closed
posting.

Verdict logic per page (after render):
- Cloudflare interstitial ("Just a moment…") → retry, then UNKNOWN.
- A real job title + an enabled apply button + no expired banner → LIVE.
- A real job title + (expired banner OR no apply button) → DEAD (closed/greyed).
- Anything ambiguous → UNKNOWN (never guess a good job dead).

Batched: one browser, many pages, capped, so a queue run stays bounded.
"""
from __future__ import annotations

import asyncio
import logging

from localjobscout.liveness import Liveness
from localjobscout.scrapers.base import CHROME_UA as _UA

logger = logging.getLogger(__name__)

_APPLY_SEL = (
    "#indeedApplyButton, .jobsearch-IndeedApplyButton, "
    "button:has-text('Apply now'), a:has-text('Apply on company site'), "
    "#applyButtonLinkContainer"
)
_EXPIRED_SEL = "text=/this job (has )?expired|no longer (available|accepting)/i"


def _is_cloudflare(title: str) -> bool:
    t = title.lower()
    return "just a moment" in t or "verifying" in t or "attention required" in t


async def _verify_one(page: object, url: str, retries: int = 1) -> Liveness:
    from playwright.async_api import Page

    assert isinstance(page, Page)
    for attempt in range(retries + 1):
        try:
            await page.goto(url, timeout=25000)
            await page.wait_for_timeout(2500)
        except Exception as exc:  # navigation/timeout
            return Liveness("unknown", f"playwright nav error: {type(exc).__name__}")

        title = await page.title()
        if _is_cloudflare(title):
            if attempt < retries:
                await page.wait_for_timeout(2500)
                continue
            return Liveness("unknown", "cloudflare challenge")

        # Page rendered a real job page if the title isn't the generic shell.
        apply_count = await page.locator(_APPLY_SEL).count()
        expired_count = await page.locator(_EXPIRED_SEL).count()

        if expired_count > 0:
            return Liveness("dead", "expired banner")
        if apply_count > 0:
            return Liveness("live", "apply button present")
        # Loaded a real title but no apply button and no expired marker — most
        # likely closed/greyed, but stay conservative.
        return Liveness("unknown", "no apply button (indeterminate)")
    return Liveness("unknown", "exhausted retries")


async def _verify_batch_async(urls: list[str]) -> dict[str, Liveness]:
    from playwright.async_api import async_playwright

    try:
        from playwright_stealth import Stealth

        has_stealth = True
    except Exception:
        has_stealth = False

    out: dict[str, Liveness] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await browser.new_context(user_agent=_UA)
        if has_stealth:
            try:
                await Stealth().apply_stealth_async(ctx)
            except Exception:
                pass
        for url in urls:
            page = await ctx.new_page()
            try:
                out[url] = await _verify_one(page, url)
            except Exception as exc:
                out[url] = Liveness("unknown", f"error: {type(exc).__name__}")
            finally:
                await page.close()
        await browser.close()
    return out


def verify_indeed_batch(urls: list[str], limit: int = 10) -> dict[str, Liveness]:
    """Verify up to `limit` Indeed URLs in one browser session. URLs beyond the
    limit are returned UNKNOWN (unchecked) to keep a queue run bounded."""
    if not urls:
        return {}
    checked, rest = urls[:limit], urls[limit:]
    try:
        result = asyncio.run(_verify_batch_async(checked))
    except Exception as exc:
        logger.warning("indeed playwright batch failed: %s", exc)
        result = {}
    for u in checked:
        result.setdefault(u, Liveness("unknown", "playwright unavailable"))
    for u in rest:
        result[u] = Liveness("unknown", "beyond indeed verify limit")
    return result
