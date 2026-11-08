"""HTTP client with TLS fingerprint impersonation and browser fallback.

Tier 1: curl_cffi — impersonates Chrome's TLS/JA3 fingerprint at the network
         layer, bypassing most Cloudflare/Akamai bot detection without a
         real browser.

Tier 2: Playwright — real Chromium browser for sites that still block
         curl_cffi. Activated automatically on 403 responses when Playwright
         is installed.
"""

from __future__ import annotations

import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from sift.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Chrome 124 on macOS — realistic TLS fingerprint target
_PLAYWRIGHT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class HttpResponse:
    """Minimal response wrapper for Playwright-fetched HTML."""

    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class CurlCffiSession:
    """Thin wrapper around curl_cffi's Session with Chrome impersonation."""

    def __init__(self):
        self._session = curl_requests.Session(impersonate="chrome124")

    def get(
        self, url: str, *, headers: dict | None = None, timeout: int = 15, **kwargs
    ) -> Optional[curl_requests.Response]:
        """Make a GET request. Returns None on connection failure."""
        try:
            return self._session.get(url, headers=headers, timeout=timeout, **kwargs)
        except Exception:
            logger.debug("curl_cffi request failed for %s", url, exc_info=True)
            return None


class BrowserFetcher:
    """Page fetcher with two-tier anti-bot strategy.

    Uses curl_cffi (Chrome TLS impersonation) as the primary method and
    automatically falls back to a real Chromium browser via Playwright when
    curl_cffi receives a 403 (bot detection) response.
    """

    def __init__(self, rate_limiter: RateLimiter, use_playwright: bool = True):
        self._rate_limiter = rate_limiter
        self._use_playwright = use_playwright
        self._curl_session: Optional[CurlCffiSession] = None
        self._pw = None
        self._browser = None
        self._context = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_html(
        self,
        url: str,
        *,
        headers: dict | None = None,
        timeout: int = 20,
        source_label: str = "fetch",
        **kwargs,
    ) -> Optional[BeautifulSoup]:
        """Two-tier HTML fetch: rate-limit, try curl_cffi, fall back to Playwright.

        Returns a parsed ``BeautifulSoup`` on success, or ``None`` if both
        tiers fail. This is the common case for plain GETs that don't
        need site-specific retry/backoff logic; callers that do (e.g.
        :class:`sift.scrapers.g2.G2Scraper`) drive curl + playwright
        manually.
        """
        self._rate_limiter.wait()

        try:
            resp = self._curl.get(url, headers=headers, timeout=timeout, **kwargs)
            if resp is not None:
                resp.raise_for_status()
                return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            logger.debug("%s curl_cffi fetch failed for %s: %s", source_label, url, e)

        pw_resp = self.fetch_playwright(url)
        if pw_resp is not None:
            logger.info("%s Playwright fallback succeeded for %s", source_label, url)
            return BeautifulSoup(pw_resp.text, "html.parser")

        logger.warning("%s failed to fetch HTML from %s", source_label, url)
        return None

    def fetch_playwright(self, url: str) -> Optional[HttpResponse]:
        """Fetch a page via a real Chromium browser (Tier 2).

        The browser is launched once and reused across calls.  Returns
        ``None`` when Playwright is not installed or the fetch fails.
        """
        if not self._use_playwright:
            return None

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "Playwright is not installed. Install with: "
                "pip install playwright && playwright install chromium"
            )
            self._use_playwright = False
            return None

        try:
            if self._asyncio_loop_running():
                return self._fetch_playwright_in_thread(url, sync_playwright)

            return self._fetch_playwright_reused(url, sync_playwright)
        except Exception:
            logger.debug("Playwright fetch failed for %s", url, exc_info=True)
            return None

    def close(self) -> None:
        """Release browser resources. Safe to call multiple times."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _asyncio_loop_running() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _fetch_playwright_reused(
        self, url: str, sync_playwright: Callable
    ) -> HttpResponse:
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(user_agent=_PLAYWRIGHT_UA)

        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            html = page.content()
            return HttpResponse(text=html, status_code=200)
        finally:
            page.close()

    def _fetch_playwright_in_thread(
        self, url: str, sync_playwright: Callable
    ) -> HttpResponse:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self._fetch_playwright_isolated,
                url,
                sync_playwright,
            )
            return future.result()

    @staticmethod
    def _fetch_playwright_isolated(
        url: str, sync_playwright: Callable
    ) -> HttpResponse:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=_PLAYWRIGHT_UA)
                try:
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        return HttpResponse(text=page.content(), status_code=200)
                    finally:
                        page.close()
                finally:
                    context.close()
            finally:
                browser.close()

    @property
    def _curl(self) -> CurlCffiSession:
        if self._curl_session is None:
            self._curl_session = CurlCffiSession()
        return self._curl_session

    @property
    def curl_session(self) -> CurlCffiSession:
        """Public accessor for the underlying curl_cffi session.

        Callers that need raw JSON/POST/proxied access (and don't want
        the curl→playwright two-tier behavior of ``fetch_html``) can
        share this session so a single TLS-impersonation handle is
        reused across all GETs from the scraper.
        """
        return self._curl
