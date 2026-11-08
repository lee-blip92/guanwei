from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

from sift.models import FeedbackItem

if TYPE_CHECKING:
    from sift.pipeline.resolver.models import SourceRef


class BaseScraper(ABC):
    # Set by the factory before scrape() runs when the resolver pipeline
    # produced a SourceRef for this scraper's source. Scrapers prefer
    # this over their config-driven defaults.
    source_ref: Optional["SourceRef"] = None

    @abstractmethod
    def scrape(self, product_name: str) -> List[FeedbackItem]:
        """Scrape feedback for a given product name."""
        pass

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the stable config key for this data source."""
        pass

    def set_source_ref(self, ref: Optional["SourceRef"]) -> None:
        self.source_ref = ref
