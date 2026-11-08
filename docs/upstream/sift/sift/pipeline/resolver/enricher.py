"""Turn a bare product name into a ProductProfile.

The enricher is the entry point of the resolver pipeline. It calls a
web search backend once to find the product's canonical homepage, then
fetches that homepage and parses Open Graph / schema.org metadata to
build a rich semantic profile.

If the user supplied a homepage hint (the ``url`` field on
``ProductIdentity``), the search step is skipped — the hint is the
homepage. This lets users bypass the resolver entirely for internal
tools whose names Brave wouldn't recognize.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from sift.pipeline.http_client import CurlCffiSession
from sift.pipeline.resolver.models import ProductProfile, SearchHit
from sift.pipeline.resolver.search.base import SearchClient

logger = logging.getLogger(__name__)


class ProductEnricher:
    def __init__(self, search_client: Optional[SearchClient] = None):
        self.search_client = search_client
        self._http = CurlCffiSession()

    def enrich(self, name: str, hint_url: Optional[str] = None) -> ProductProfile:
        snippets: List[str] = []
        homepage = hint_url
        backend_used = "hint" if hint_url else ""

        if not homepage and self.search_client is not None:
            hits = self.search_client.web_search(name, limit=5)
            backend_used = self.search_client.backend_name
            snippets = [f"{h.title} — {h.snippet}".strip(" —") for h in hits if h.snippet]
            homepage = _pick_homepage(name, hits)

        og_data = self._fetch_og_data(homepage) if homepage else {}
        description = (
            og_data.get("og:description")
            or og_data.get("description")
            or (snippets[0] if snippets else "")
        )
        category = (
            og_data.get("og:type")
            or og_data.get("category")
            or _infer_category_from_snippets(snippets)
        )

        return ProductProfile(
            name=name,
            homepage=homepage,
            description=description.strip(),
            category=category.strip(),
            site_name=(og_data.get("og:site_name") or "").strip(),
            search_snippets=snippets,
            raw_metadata=og_data,
            enricher_used=backend_used,
        )

    def _fetch_og_data(self, url: str) -> dict:
        resp = self._http.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp is None or resp.status_code != 200:
            return {}
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            return {}
        meta: dict[str, str] = {}
        for tag in soup.find_all("meta"):
            key = tag.get("property") or tag.get("name")
            value = tag.get("content")
            if key and value and key not in meta:
                meta[key] = value
        # JSON-LD frequently carries `category`, `applicationCategory`,
        # `description` for SaaS/SoftwareApplication entities.
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            _collect_ld(data, meta)
        return meta


def _pick_homepage(name: str, hits: List[SearchHit]) -> Optional[str]:
    """Pick the most likely homepage from search hits.

    Heuristic: the top hit whose hostname looks like a brand domain
    (short, contains the name, not a known aggregator). Falls back to
    the top hit if nothing matches.
    """
    if not hits:
        return None
    name_token = re.sub(r"[^a-z0-9]+", "", name.lower())
    aggregators = {
        "wikipedia.org", "g2.com", "producthunt.com", "capterra.com",
        "reddit.com", "youtube.com", "linkedin.com", "twitter.com",
        "x.com", "facebook.com", "crunchbase.com", "github.com",
    }
    for hit in hits:
        host = urlparse(hit.url).hostname or ""
        host = host.removeprefix("www.")
        if any(host.endswith(a) for a in aggregators):
            continue
        host_token = re.sub(r"[^a-z0-9]+", "", host.split(".")[0].lower())
        if name_token and (name_token in host_token or host_token in name_token):
            return f"https://{host}"
    # Nothing brand-like — return the top hit's host as a best guess.
    host = (urlparse(hits[0].url).hostname or "").removeprefix("www.")
    return f"https://{host}" if host else hits[0].url


def _collect_ld(node, meta: dict) -> None:
    if isinstance(node, dict):
        for key in ("description", "category", "applicationCategory"):
            value = node.get(key)
            if isinstance(value, str) and key not in meta:
                meta[key] = value
        for value in node.values():
            _collect_ld(value, meta)
    elif isinstance(node, list):
        for item in node:
            _collect_ld(item, meta)


_CATEGORY_KEYWORDS = {
    "productivity": ["productivity", "workspace", "notes", "docs", "task"],
    "developer-tools": ["developer", "api", "sdk", "ide", "code", "git"],
    "design": ["design", "prototype", "figma", "ui"],
    "analytics": ["analytics", "dashboard", "metrics", "insights"],
    "communication": ["chat", "messaging", "video", "meeting"],
    "marketing": ["marketing", "crm", "email", "campaign"],
    "security": ["security", "encryption", "auth", "compliance"],
}


def _infer_category_from_snippets(snippets: List[str]) -> str:
    text = " ".join(snippets).lower()
    for category, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return category
    return ""
