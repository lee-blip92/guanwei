import logging
from typing import List
from sift.models import FeedbackItem

logger = logging.getLogger(__name__)


class DedupFilter:
    """Tracks seen feedback IDs to prevent duplicates across scrapers and sessions."""

    def __init__(self):
        self._seen_ids: set[str] = set()

    def filter(self, items: List[FeedbackItem]) -> List[FeedbackItem]:
        """Return only items whose IDs haven't been seen yet, updating the seen set."""
        unique: List[FeedbackItem] = []
        for item in items:
            if item.id not in self._seen_ids:
                self._seen_ids.add(item.id)
                unique.append(item)

        duplicates = len(items) - len(unique)
        if duplicates > 0:
            logger.info("Deduplication filtered %d duplicate item(s)", duplicates)

        return unique

    def reset(self) -> None:
        """Clear the seen set for a fresh session."""
        self._seen_ids.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_ids)
