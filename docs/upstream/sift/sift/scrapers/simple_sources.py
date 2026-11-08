import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Iterable, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from sift.config import (
    AppStoreConfig,
    ChangelogsConfig,
    DevToConfig,
    DiscordExportsConfig,
    GitHubIssuesConfig,
    HackerNewsConfig,
    LinkedInCommentsConfig,
    PlayStoreConfig,
    ProductHuntConfig,
    StackOverflowConfig,
    SupportForumsConfig,
    YouTubeConfig,
)
from sift.models import FeedbackItem
from sift.pipeline.http_client import BrowserFetcher
from sift.pipeline.rate_limiter import RateLimiter
from sift.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class RequestsScraper(BaseScraper):
    def __init__(self, max_requests_per_minute: int = 20, request_delay: float = 1.0, use_playwright: bool = True):
        jitter = max(0.1, request_delay)
        self.rate_limiter = RateLimiter(
            max_requests_per_minute=max_requests_per_minute,
            jitter_range=(max(0.1, jitter * 0.75), jitter * 1.25),
            max_retries=2,
        )
        self._browser_fetcher = BrowserFetcher(self.rate_limiter, use_playwright=use_playwright)
        self._curl_session = self._browser_fetcher.curl_session

    def _get_json(self, url: str, **kwargs) -> Optional[Any]:
        self.rate_limiter.wait()
        try:
            resp = self._curl_session.get(url, timeout=20, **kwargs)
            if resp is None:
                logger.warning("%s connection failed for %s", self.source_name, url)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("%s failed to fetch JSON from %s: %s", self.source_name, url, e)
            return None

    def _post_json(self, url: str, json_data: dict, headers: dict | None = None) -> Optional[Any]:
        self.rate_limiter.wait()
        try:
            resp = self._curl_session._session.post(url, json=json_data, headers=headers, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("%s POST to %s failed: %s", self.source_name, url, e)
            return None

    def _get_html(self, url: str, **kwargs) -> Optional[BeautifulSoup]:
        return self._browser_fetcher.fetch_html(
            url, source_label=self.source_name, **kwargs
        )


class AppStoreScraper(RequestsScraper):
    def __init__(self, config: AppStoreConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "app_store"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        app_id = (
            (self.source_ref.identifier if self.source_ref else None)
            or self.config.app_ids.get(product_name)
        )
        if not app_id:
            logger.info("App Store app ID not configured for '%s', skipping.", product_name)
            return []

        items: List[FeedbackItem] = []
        for country in self.config.countries:
            for page in range(1, self.config.max_pages + 1):
                url = (
                    f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/"
                    f"id={app_id}/sortby=mostrecent/json"
                )
                data = self._get_json(url)
                entries = data.get("feed", {}).get("entry", []) if isinstance(data, dict) else []
                if isinstance(entries, dict):
                    entries = [entries]
                for entry in entries:
                    if "im:name" in entry:
                        continue
                    content = entry.get("content", {}).get("label", "")
                    title = entry.get("title", {}).get("label", "")
                    text = f"{title}\n\n{content}".strip()
                    if len(text) < 20:
                        continue
                    rating = _safe_float(entry.get("im:rating", {}).get("label"))
                    review_url = entry.get("link", {}).get("attributes", {}).get("href", url)
                    items.append(FeedbackItem(
                        source=self.source_name,
                        product=product_name,
                        text=text,
                        rating=rating,
                        url=review_url,
                        metadata={"country": country, "page": page},
                    ))
                    if len(items) >= self.config.max_items:
                        return items
        return items


class PlayStoreScraper(RequestsScraper):
    def __init__(self, config: PlayStoreConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "play_store"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        package_name = (
            (self.source_ref.identifier if self.source_ref else None)
            or self.config.package_names.get(product_name)
        )
        if not package_name:
            logger.info("Play Store package name not configured for '%s', skipping.", product_name)
            return []

        items: List[FeedbackItem] = []
        for country in self.config.countries:
            for language in self.config.languages:
                url = (
                    "https://play.google.com/store/apps/details"
                    f"?id={quote_plus(package_name)}&hl={language}&gl={country}&showAllReviews=true"
                )
                soup = self._get_html(url, headers={"User-Agent": "Mozilla/5.0"})
                if not soup:
                    continue
                for review in soup.select("[data-review-id]"):
                    text = review.get_text(" ", strip=True)
                    if len(text) < 20:
                        continue
                    items.append(FeedbackItem(
                        source=self.source_name,
                        product=product_name,
                        text=text,
                        url=url,
                        metadata={"country": country, "language": language},
                    ))
                    if len(items) >= self.config.max_items:
                        return items
        return items


class YouTubeScraper(RequestsScraper):
    def __init__(self, config: YouTubeConfig):
        super().__init__(max_requests_per_minute=60, request_delay=0.5)
        self.config = config

    @property
    def source_name(self) -> str:
        return "youtube"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        if not self.config.api_key:
            logger.info("YOUTUBE_API_KEY not set, skipping YouTube comments.")
            return []

        video_ids = self.config.video_ids.get(product_name, [])[: self.config.max_videos]
        if not video_ids:
            logger.info("YouTube video IDs not configured for '%s', skipping.", product_name)
            return []

        items: List[FeedbackItem] = []
        for video_id in video_ids:
            url = "https://www.googleapis.com/youtube/v3/commentThreads"
            params = {
                "part": "snippet",
                "videoId": video_id,
                "maxResults": min(100, self.config.max_comments_per_video),
                "order": self.config.order,
                "textFormat": "plainText",
                "key": self.config.api_key,
            }
            data = self._get_json(url, params=params)
            for item in data.get("items", []) if isinstance(data, dict) else []:
                snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                text = snippet.get("textDisplay", "")
                if len(text) < 20:
                    continue
                comment_id = item.get("snippet", {}).get("topLevelComment", {}).get("id", "")
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}",
                    date=_parse_datetime(snippet.get("publishedAt")),
                    metadata={"video_id": video_id, "like_count": snippet.get("likeCount")},
                ))
                if len(items) >= self.config.max_comments_per_video * max(1, len(video_ids)):
                    return items
        return items


class HackerNewsScraper(RequestsScraper):
    def __init__(self, config: HackerNewsConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "hacker_news"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        items: List[FeedbackItem] = []
        cutoff = int(time.time()) - self.config.recency_days * 86400
        recency = quote_plus(f"created_at_i>{cutoff}")

        # search_by_date returns newest first; recency filter drops stale results.
        # Search stories first, then fetch their comment trees — direct comment
        # search misses mentions where the story title carries the product name.
        story_url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={quote_plus(product_name)}&tags=story"
            f"&hitsPerPage={min(10, self.config.max_items)}"
            f"&numericFilters={recency}"
        )
        story_data = self._get_json(story_url)
        for hit in story_data.get("hits", []) if isinstance(story_data, dict) else []:
            object_id = hit.get("objectID")
            title = _html_to_text(hit.get("title") or hit.get("story_title") or "")
            story_text = _html_to_text(hit.get("story_text") or "")
            if title or story_text:
                text = f"{title}\n\n{story_text}".strip()
                if len(text) >= 20:
                    items.append(self._feedback_from_hn_hit(product_name, hit, text))
            if object_id:
                items.extend(self._comments_for_story(product_name, object_id, title))
            if len(items) >= self.config.max_items:
                return items[: self.config.max_items]

        comment_url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={quote_plus(product_name)}&tags=comment"
            f"&hitsPerPage={self.config.max_items}"
            f"&numericFilters={recency}"
        )
        comment_data = self._get_json(comment_url)
        for hit in comment_data.get("hits", []) if isinstance(comment_data, dict) else []:
            text = _html_to_text(hit.get("comment_text") or "")
            if len(text) < 20:
                continue
            items.append(self._feedback_from_hn_hit(product_name, hit, text))
            if len(items) >= self.config.max_items:
                break
        return items[: self.config.max_items]

    def _comments_for_story(
        self, product_name: str, story_id: str, story_title: str
    ) -> List[FeedbackItem]:
        data = self._get_json(f"https://hn.algolia.com/api/v1/items/{story_id}")
        if not isinstance(data, dict):
            return []
        return [
            FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=text,
                url=f"https://news.ycombinator.com/item?id={comment_id}",
                metadata={"story_id": story_id, "story_title": story_title},
            )
            for comment_id, text in _walk_hn_comments(data.get("children", []))
            if len(text) >= 20
        ]

    def _feedback_from_hn_hit(
        self, product_name: str, hit: dict, text: str
    ) -> FeedbackItem:
        object_id = hit.get("objectID")
        return FeedbackItem(
            source=self.source_name,
            product=product_name,
            text=text,
            url=f"https://news.ycombinator.com/item?id={object_id}" if object_id else None,
            date=_parse_datetime(hit.get("created_at")),
            metadata={"points": hit.get("points"), "story_title": hit.get("story_title")},
        )


class GitHubIssuesScraper(RequestsScraper):
    def __init__(self, config: GitHubIssuesConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "github_issues"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        repos = list(self.config.repos.get(product_name, []))
        if not repos and self.source_ref:
            # Resolver gave us a single owner/repo — wrap it for _scrape_repos.
            repos = [self.source_ref.identifier]
        if repos:
            return self._scrape_repos(product_name, repos, headers)
        return self._search_all_github(product_name, headers)

    def _scrape_repos(
        self, product_name: str, repos: List[str], headers: dict
    ) -> List[FeedbackItem]:
        items: List[FeedbackItem] = []
        for repo in repos:
            query = quote_plus(f"repo:{repo} {product_name} is:issue")
            url = f"https://api.github.com/search/issues?q={query}&sort=updated&order=desc&per_page=50"
            data = self._get_json(url, headers=headers)
            for issue in data.get("items", []) if isinstance(data, dict) else []:
                text = f"{issue.get('title', '')}\n\n{issue.get('body') or ''}".strip()
                if len(text) < 20:
                    continue
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=issue.get("html_url"),
                    date=_parse_datetime(issue.get("created_at")),
                    metadata={"repo": repo, "state": issue.get("state"), "comments": issue.get("comments")},
                ))
                if len(items) >= self.config.max_items:
                    return items
        return items

    def _search_all_github(self, product_name: str, headers: dict) -> List[FeedbackItem]:
        """Search all of GitHub for issues mentioning the product (no repos needed)."""
        query = quote_plus(f'"{product_name}" in:title is:issue')
        url = (
            f"https://api.github.com/search/issues"
            f"?q={query}&sort=created&order=desc&per_page={self.config.max_items}"
        )
        data = self._get_json(url, headers=headers)
        items: List[FeedbackItem] = []
        for issue in data.get("items", []) if isinstance(data, dict) else []:
            text = f"{issue.get('title', '')}\n\n{issue.get('body') or ''}".strip()
            if len(text) < 20:
                continue
            repo_url = issue.get("repository_url", "")
            repo = "/".join(repo_url.split("/")[-2:]) if repo_url else ""
            items.append(FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=text,
                url=issue.get("html_url"),
                date=_parse_datetime(issue.get("created_at")),
                metadata={"repo": repo, "state": issue.get("state"), "comments": issue.get("comments")},
            ))
            if len(items) >= self.config.max_items:
                break
        logger.info("GitHub Issues: collected %d issues for '%s'", len(items), product_name)
        return items


_PH_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"


class _PHTransientError(Exception):
    """Raised by ``_post_ph_graphql`` for network / HTTP failures.

    Distinct from ``None`` (which signals a 401 → retry with a refreshed
    token) so callers can tell a transient failure apart from a real
    "no post found" response and log accordingly.
    """


# PH API v2 has no full-text search on `posts` (ARCHITECTURE.md
# Decision 042 — schema introspection confirmed `posts` only accepts
# filter args, no `search`) and no `reviews` type — only `Comment`. We
# look up the launch post by slug and pull its discussion thread.
# Reviews proper are only available via the web page, which the
# __NEXT_DATA__ fallback handles. When the slug guess misses we surface
# an actionable log line pointing at the resolver / manual config
# rather than silently returning [].
_PH_POST_QUERY = """
query($slug: String!, $first: Int!) {
  post(slug: $slug) {
    id
    name
    slug
    tagline
    description
    url
    votesCount
    commentsCount
    comments(first: $first, order: VOTES_COUNT) {
      edges {
        node {
          id
          body
          votesCount
          createdAt
          url
        }
      }
    }
  }
}
"""


class ProductHuntScraper(RequestsScraper):
    def __init__(self, config: ProductHuntConfig):
        super().__init__(
            config.max_requests_per_minute,
            config.request_delay,
            use_playwright=config.use_playwright_fallback,
        )
        self.config = config
        self._access_token: Optional[str] = None

    @property
    def source_name(self) -> str:
        return "product_hunt"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        if self.config.client_id and self.config.client_secret:
            items = self._scrape_via_api(product_name)
            if items:
                return items
            logger.warning("Product Hunt API returned no results for '%s', falling back to HTML", product_name)
        return self._scrape_via_html(product_name)

    def _get_access_token(self, force_refresh: bool = False) -> Optional[str]:
        """Mint and cache a client-credentials bearer token.

        Uses PH's OAuth 2.0 client-credentials flow, which grants read
        access to public endpoints only — no user context, safe to ship
        with the app. Token is cached on the instance and only refreshed
        on 401 (or when ``force_refresh`` is set).
        """
        if self._access_token and not force_refresh:
            return self._access_token
        payload = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "grant_type": "client_credentials",
        }
        data = self._post_json("https://api.producthunt.com/v2/oauth/token", payload)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            logger.warning("Product Hunt: failed to obtain client-credentials token")
            return None
        self._access_token = token
        return token

    def _scrape_via_api(self, product_name: str) -> List[FeedbackItem]:
        token = self._get_access_token()
        if not token:
            return []
        # Slug precedence: resolver-confirmed → user-configured → name guess.
        # PH v2 has no `search` on the posts query (Decision 042) so when the
        # guess misses, the API path is exhausted — log an actionable hint
        # instead of silently returning [].
        confirmed_slug = (self.source_ref.identifier if self.source_ref else None) or \
            self.config.slugs.get(product_name)
        slug = confirmed_slug or product_name.lower().replace(" ", "-")
        try:
            post = self._post_by_slug(slug, token)
        except _PHTransientError as e:
            logger.warning("%s", e)
            return []
        if post is None:
            if confirmed_slug is None:
                logger.warning(
                    "Product Hunt: no post for guessed slug '%s' (from product '%s'). "
                    "Add product_hunt.slugs['%s'] = '<real-slug>' to config.yaml, "
                    "or enable the resolver to auto-discover it.",
                    slug, product_name, product_name,
                )
            else:
                logger.info(
                    "Product Hunt: no post found for slug '%s' (configured for '%s')",
                    slug, product_name,
                )
            return []

        items: List[FeedbackItem] = []
        post_name = post.get("name", "")
        post_url = f"https://www.producthunt.com/posts/{slug}"
        tagline = post.get("tagline", "")
        description = post.get("description", "")

        if tagline and len(tagline) >= 20:
            items.append(FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=f"{post_name}\n\n{tagline}".strip(),
                url=post_url,
                metadata={"slug": slug, "type": "tagline"},
            ))
        if description and len(description) >= 20:
            items.append(FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=description,
                url=post_url,
                metadata={"slug": slug, "type": "description"},
            ))

        for edge in (post.get("comments") or {}).get("edges", []):
            node = edge.get("node") or {}
            body = node.get("body", "")
            if len(body) < 20:
                continue
            items.append(FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=body,
                url=node.get("url") or post_url,
                date=_parse_datetime(node.get("createdAt")),
                metadata={
                    "slug": slug,
                    "type": "comment",
                    "votes_count": node.get("votesCount"),
                },
            ))
            if len(items) >= self.config.max_items:
                break

        logger.info("Product Hunt API: collected %d items for '%s'", len(items), product_name)
        return items

    def _post_by_slug(self, slug: str, token: str) -> Optional[dict]:
        """Run ``_PH_POST_QUERY`` with a one-shot token-refresh on 401.

        Returns the ``post`` node (or None when GraphQL replies but the
        slug doesn't exist). Raises ``_PHTransientError`` on network /
        HTTP failures.
        """
        payload = {
            "query": _PH_POST_QUERY,
            "variables": {"slug": slug, "first": min(50, self.config.max_items)},
        }
        data = self._post_ph_graphql(payload, token)
        if data is None:
            refreshed = self._get_access_token(force_refresh=True)
            if not refreshed:
                return None
            data = self._post_ph_graphql(payload, refreshed)
        if not isinstance(data, dict):
            return None
        if data.get("errors"):
            logger.warning(
                "Product Hunt GraphQL errors for slug '%s': %s", slug, data["errors"]
            )
            return None
        return (data.get("data") or {}).get("post")

    def _post_ph_graphql(self, payload: dict, token: str) -> Optional[Any]:
        """POST a GraphQL query. Returns None on 401 so the caller can refresh."""
        self.rate_limiter.wait()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            resp = self._curl_session._session.post(
                _PH_GRAPHQL_URL, json=payload, headers=headers, timeout=20
            )
        except Exception as e:
            raise _PHTransientError(f"Product Hunt GraphQL request failed: {e}") from e
        if resp.status_code == 401:
            return None
        try:
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise _PHTransientError(
                f"Product Hunt GraphQL HTTP {resp.status_code}: {e}"
            ) from e

    def _scrape_via_html(self, product_name: str) -> List[FeedbackItem]:
        # Product Hunt is a Next.js SPA — selectors against the raw HTML
        # return nothing because comments hydrate client-side. Instead,
        # extract __NEXT_DATA__ (the server-embedded JSON Next.js ships
        # with every page) and walk it for review/comment nodes.
        slug = (
            (self.source_ref.identifier if self.source_ref else None)
            or self.config.slugs.get(product_name)
            or product_name.lower().replace(" ", "-")
        )
        items: List[FeedbackItem] = []
        seen_texts: set[str] = set()

        for path in (f"/products/{slug}/reviews", f"/products/{slug}"):
            url = f"https://www.producthunt.com{path}"
            soup = self._get_html(url, headers={"User-Agent": "Mozilla/5.0"})
            if not soup:
                continue

            title = (soup.title.string or "") if soup.title else ""
            if "just a moment" in title.lower() or soup.find(id="challenge-stage"):
                logger.warning(
                    "Product Hunt: Cloudflare challenge intercepted %s — "
                    "HTML fallback needs a stealth browser (camoufox / patchright) "
                    "or a paid unblocker. Configure PH slugs and rely on the API instead.",
                    url,
                )
                continue

            payload = self._extract_next_data(soup)
            if payload is None:
                logger.debug("Product Hunt: no __NEXT_DATA__ on %s", url)
                continue

            needle = product_name.lower()
            for text, node_url, metadata in _walk_ph_feedback(payload, slug):
                if len(text) < 20 or text in seen_texts:
                    continue
                # Per-product topicality — PH's __NEXT_DATA__ also embeds
                # related-product comments and sidebar threads, which would
                # otherwise pollute the FeedbackItem list when the slug guess
                # lands on a wrong page.
                if needle not in text.lower():
                    continue
                seen_texts.add(text)
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=node_url or url,
                    rating=metadata.pop("rating", None),
                    metadata={"slug": slug, **metadata},
                ))
                if len(items) >= self.config.max_items:
                    logger.info("Product Hunt HTML: collected %d items for '%s'", len(items), product_name)
                    return items

        logger.info("Product Hunt HTML: collected %d items for '%s'", len(items), product_name)
        return items

    @staticmethod
    def _extract_next_data(soup: BeautifulSoup) -> Optional[Any]:
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            return None
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            return None


class SearchPageScraper(RequestsScraper):
    config: Any

    def _search_urls(self, product_name: str) -> Iterable[str]:
        for template in self.config.search_urls:
            yield template.format(query=quote_plus(product_name), product=quote_plus(product_name))

    def _items_from_pages(self, product_name: str) -> List[FeedbackItem]:
        items: List[FeedbackItem] = []
        for url in self._search_urls(product_name):
            soup = self._get_html(url, headers={"User-Agent": "Mozilla/5.0"})
            if not soup:
                continue
            for element in soup.select("article, main p, .post, .topic, .comment, li"):
                text = element.get_text(" ", strip=True)
                if len(text) < 40 or product_name.lower() not in text.lower():
                    continue
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=url,
                ))
                if len(items) >= self.config.max_items:
                    return items
        return items


class SupportForumsScraper(SearchPageScraper):
    def __init__(self, config: SupportForumsConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "support_forums"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        if not self.config.search_urls:
            logger.info("Support forum search URLs not configured, skipping.")
            return []
        return self._items_from_pages(product_name)


class ChangelogScraper(SearchPageScraper):
    def __init__(self, config: ChangelogsConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "changelogs"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        urls = self.config.urls.get(product_name, [])
        items: List[FeedbackItem] = []
        for url in urls:
            soup = self._get_html(url, headers={"User-Agent": "Mozilla/5.0"})
            if not soup:
                continue
            for element in soup.select("article, main, section, li, p"):
                text = element.get_text(" ", strip=True)
                if len(text) < 40:
                    continue
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=url,
                ))
                if len(items) >= self.config.max_items:
                    return items

        if self.config.search_urls and len(items) < self.config.max_items:
            items.extend(self._items_from_pages(product_name))
        return items[: self.config.max_items]


class StackOverflowScraper(RequestsScraper):
    def __init__(self, config: StackOverflowConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "stack_overflow"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        items: List[FeedbackItem] = []
        for site in self.config.sites:
            params = (
                f"q={quote_plus(product_name)}"
                f"&site={quote_plus(site)}"
                f"&order=desc&sort=creation"
                f"&pagesize={min(30, self.config.max_items)}"
            )
            if self.config.api_key:
                params += f"&key={quote_plus(self.config.api_key)}"
            url = f"https://api.stackexchange.com/2.3/search/excerpts?{params}"
            data = self._get_json(url)
            if not isinstance(data, dict):
                continue
            for item in data.get("items", []):
                title = item.get("title", "")
                excerpt = _html_to_text(item.get("excerpt") or "")
                text = f"{title}\n\n{excerpt}".strip() if title else excerpt
                if len(text) < 20:
                    continue
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=item.get("link"),
                    metadata={"site": site, "item_type": item.get("item_type")},
                ))
                if len(items) >= self.config.max_items:
                    return items
        logger.info("Stack Overflow: collected %d items for '%s'", len(items), product_name)
        return items


class DevToScraper(RequestsScraper):
    def __init__(self, config: DevToConfig):
        super().__init__(config.max_requests_per_minute, config.request_delay)
        self.config = config

    @property
    def source_name(self) -> str:
        return "dev_to"

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        items: List[FeedbackItem] = []

        # Preferred path: resolver gave us a tag → use Dev.to's documented
        # articles API, which is structured and reliable.
        if self.source_ref and self.source_ref.identifier:
            tag = self.source_ref.identifier
            tag_url = f"https://dev.to/api/articles?tag={quote_plus(tag)}&per_page={self.config.max_items}"
            data = self._get_json(tag_url)
            for article in data or []:
                title = article.get("title", "")
                description = article.get("description") or ""
                text = f"{title}\n\n{description}".strip()
                if len(text) < 20:
                    continue
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=article.get("url"),
                    date=_parse_datetime(article.get("published_at")),
                    metadata={"tag_list": article.get("tag_list", []), "via": "tag"},
                ))
                if len(items) >= self.config.max_items:
                    break

            # Also pull resolver-supplied article URLs (Brave site-search hits)
            # — they may not carry the tag but match the product name directly.
            for art_url in (self.source_ref.payload.get("article_urls") or [])[:5]:
                if len(items) >= self.config.max_items:
                    break
                slug = art_url.rstrip("/").split("/")[-1]
                api_url = f"https://dev.to/api/articles/{quote_plus(slug)}"
                detail = self._get_json(api_url)
                if not isinstance(detail, dict):
                    continue
                title = detail.get("title", "")
                description = detail.get("description") or ""
                text = f"{title}\n\n{description}".strip()
                if len(text) < 20:
                    continue
                items.append(FeedbackItem(
                    source=self.source_name,
                    product=product_name,
                    text=text,
                    url=detail.get("url"),
                    date=_parse_datetime(detail.get("published_at")),
                    metadata={"tag_list": detail.get("tag_list", []), "via": "article_url"},
                ))

            logger.info("Dev.to: collected %d articles via tag '%s'", len(items), tag)
            return items

        # Fallback: legacy text-search endpoint (often empty, but kept for
        # users without a resolver configured).
        url = (
            "https://dev.to/search/feed_content"
            f"?per_page={self.config.max_items}"
            f"&q={quote_plus(product_name)}"
            "&content_type=article"
        )
        data = self._get_json(url)
        for result in (data or {}).get("result", []):
            title = result.get("title", "")
            description = result.get("description") or result.get("body_preview") or ""
            text = f"{title}\n\n{description}".strip()
            if len(text) < 20:
                continue
            path = result.get("path", "")
            items.append(FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=text,
                url=f"https://dev.to{path}" if path else None,
                metadata={"tag_list": result.get("tag_list", []), "via": "search"},
            ))
            if len(items) >= self.config.max_items:
                break
        logger.info("Dev.to: collected %d articles for '%s'", len(items), product_name)
        return items


class JsonExportScraper(RequestsScraper):
    def __init__(self, paths: List[str], urls: List[str], max_items: int):
        # Route through the shared rate-limited curl_cffi session so the
        # Discord/LinkedIn export URLs share the same TLS impersonation
        # and per-source token budget as every other HTTP scraper.
        # Playwright is overkill for static JSON dumps.
        super().__init__(use_playwright=False)
        self.paths = paths
        self.urls = urls
        self.max_items = max_items

    def scrape(self, product_name: str) -> List[FeedbackItem]:
        items: List[FeedbackItem] = []
        for path in self.paths:
            if not os.path.exists(path):
                logger.warning("%s export path does not exist: %s", self.source_name, path)
                continue
            with open(path, "r") as f:
                raw = json.load(f)
            items.extend(self._parse_records(raw, product_name, path))
            if len(items) >= self.max_items:
                return items[: self.max_items]

        for url in self.urls:
            raw = self._get_json(url)
            if raw is None:
                continue
            items.extend(self._parse_records(raw, product_name, url))
        return items[: self.max_items]

    def _parse_records(self, raw: Any, product_name: str, origin: str) -> List[FeedbackItem]:
        records = raw if isinstance(raw, list) else raw.get("messages", raw.get("comments", [])) if isinstance(raw, dict) else []
        items: List[FeedbackItem] = []
        for record in records:
            text = self._record_text(record)
            if len(text) < 20 or product_name.lower() not in text.lower():
                continue
            items.append(FeedbackItem(
                source=self.source_name,
                product=product_name,
                text=text,
                url=record.get("url") if isinstance(record, dict) else origin,
                date=_parse_datetime(record.get("timestamp") or record.get("created_at")) if isinstance(record, dict) else None,
                metadata={"origin": origin},
            ))
        return items

    def _record_text(self, record: Any) -> str:
        if isinstance(record, str):
            return record
        if not isinstance(record, dict):
            return ""
        value = record.get("content") or record.get("text") or record.get("body") or record.get("comment") or ""
        if isinstance(value, list):
            return " ".join(str(item) for item in value)
        return str(value)


class DiscordExportsScraper(JsonExportScraper):
    def __init__(self, config: DiscordExportsConfig):
        super().__init__(config.paths, config.urls, config.max_items)

    @property
    def source_name(self) -> str:
        return "discord_exports"


class LinkedInCommentsScraper(JsonExportScraper):
    def __init__(self, config: LinkedInCommentsConfig):
        super().__init__(config.paths, config.urls, config.max_items)

    @property
    def source_name(self) -> str:
        return "linkedin_comments"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _html_to_text(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value, "html.parser").get_text(" ", strip=True)).strip()


_PH_TEXT_KEYS = ("body", "bodyHtml", "comment", "text", "review", "reviewBody")
_PH_FEEDBACK_TYPENAMES = {"Comment", "Review", "ProductReview", "Thread"}


def _walk_ph_feedback(
    node: Any, slug: str
) -> Iterable[tuple[str, Optional[str], dict]]:
    """Yield (text, url, metadata) tuples from a Product Hunt __NEXT_DATA__ tree.

    PH's Next.js payload nests reviews and comments under varying keys
    depending on the page. Rather than hard-coding a path that will break
    on their next deploy, walk the tree and emit any dict that looks like
    a feedback node — identified by __typename or by having a body-like
    field plus an id.
    """
    if isinstance(node, dict):
        typename = node.get("__typename")
        text = ""
        for key in _PH_TEXT_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                text = _html_to_text(value) if "<" in value else value.strip()
                break

        looks_like_feedback = (
            typename in _PH_FEEDBACK_TYPENAMES
            or (text and any(k in node for k in ("id", "createdAt", "votesCount", "rating")))
        )
        if looks_like_feedback and text:
            node_id = node.get("id") or node.get("slug")
            url = None
            if typename == "Comment" and node_id:
                url = f"https://www.producthunt.com/products/{slug}/reviews?comment={node_id}"
            elif typename in {"Review", "ProductReview"} and node_id:
                url = f"https://www.producthunt.com/products/{slug}/reviews/{node_id}"
            metadata = {"type": (typename or "feedback").lower()}
            rating = node.get("rating")
            if isinstance(rating, (int, float)):
                metadata["rating"] = float(rating)
            votes = node.get("votesCount")
            if isinstance(votes, int):
                metadata["votes_count"] = votes
            yield text, url, metadata

        for value in node.values():
            yield from _walk_ph_feedback(value, slug)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_ph_feedback(item, slug)


def _walk_hn_comments(children: list) -> Iterable[tuple[str, str]]:
    for child in children:
        if not isinstance(child, dict):
            continue
        text = _html_to_text(child.get("text") or "")
        comment_id = str(child.get("id") or "")
        if text:
            yield comment_id, text
        yield from _walk_hn_comments(child.get("children", []))
