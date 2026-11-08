from sift.pipeline.resolver.sources.app_store import AppStoreResolver
from sift.pipeline.resolver.sources.base import SourceResolver
from sift.pipeline.resolver.sources.dev_to import DevToResolver
from sift.pipeline.resolver.sources.g2 import G2Resolver
from sift.pipeline.resolver.sources.github_issues import GitHubIssuesResolver
from sift.pipeline.resolver.sources.play_store import PlayStoreResolver
from sift.pipeline.resolver.sources.product_hunt import ProductHuntResolver

__all__ = [
    "SourceResolver",
    "ProductHuntResolver",
    "G2Resolver",
    "DevToResolver",
    "GitHubIssuesResolver",
    "AppStoreResolver",
    "PlayStoreResolver",
]


def build_default_resolvers(search_client, disambiguator):
    """Construct the standard registry of per-source resolvers."""
    return {
        "product_hunt": ProductHuntResolver(search_client, disambiguator),
        "g2": G2Resolver(search_client, disambiguator),
        "dev_to": DevToResolver(search_client, disambiguator),
        "github_issues": GitHubIssuesResolver(search_client, disambiguator),
        "app_store": AppStoreResolver(search_client, disambiguator),
        "play_store": PlayStoreResolver(search_client, disambiguator),
    }
