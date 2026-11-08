import time
import random
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter with jitter for polite web scraping."""

    def __init__(
        self,
        max_requests_per_minute: int = 50,
        jitter_range: tuple = (0.5, 1.5),
        backoff_base: float = 2.0,
        max_backoff: float = 60.0,
        max_retries: int = 3,
    ):
        self.max_requests_per_minute = max_requests_per_minute
        self.min_interval = 60.0 / max_requests_per_minute
        self.jitter_range = jitter_range
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.max_retries = max_retries
        self._last_request_time: Optional[float] = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Wait before making the next request, with jitter."""
        with self._lock:
            if self._last_request_time is not None:
                elapsed = time.time() - self._last_request_time
                jitter = random.uniform(*self.jitter_range)
                required_wait = self.min_interval * jitter
                if elapsed < required_wait:
                    sleep_time = required_wait - elapsed
                    time.sleep(sleep_time)
            self._last_request_time = time.time()

    def backoff(self, attempt: int, source: str = "") -> float:
        """Calculate exponential backoff delay with jitter for retry attempts."""
        delay = min(self.backoff_base ** attempt, self.max_backoff)
        jitter = random.uniform(0.8, 1.2)
        backoff_delay = delay * jitter
        if source:
            logger.warning(
                "%s rate limited. Waiting %.1fs before retry %d/%d.",
                source, backoff_delay, attempt + 1, self.max_retries,
            )
        else:
            logger.warning(
                "Rate limited. Waiting %.1fs before retry %d/%d.",
                backoff_delay, attempt + 1, self.max_retries,
            )
        time.sleep(backoff_delay)
        return backoff_delay

    def should_retry(self, attempt: int) -> bool:
        """Check if we should retry based on attempt count."""
        return attempt < self.max_retries


class PrawRateMonitor:
    """Monitor PRAW's rate limit headers from Reddit API responses."""

    def __init__(self, target_rate: int = 80):
        self.target_rate = target_rate  # requests per minute (leave headroom below 100 QPM)
        self._request_count = 0
        self._start_time: Optional[float] = None

    def record_request(self) -> None:
        """Record a request and log rate info periodically."""
        now = time.time()
        if self._start_time is None:
            self._start_time = now
        self._request_count += 1

        elapsed = now - self._start_time
        if elapsed > 0 and self._request_count % 10 == 0:
            current_rate = self._request_count / elapsed * 60
            logger.debug(
                "%d Reddit requests in %.0fs (current rate: %.1f/min, target: %d/min)",
                self._request_count, elapsed, current_rate, self.target_rate,
            )

    def ensure_pace(self) -> None:
        """Sleep if we're exceeding the target rate."""
        if self._start_time is None:
            self._start_time = time.time()
            return

        elapsed = time.time() - self._start_time
        if elapsed > 0:
            current_rate = self._request_count / elapsed * 60
            if current_rate > self.target_rate:
                overshoot_ratio = current_rate / self.target_rate
                sleep_time = (overshoot_ratio - 1) * 2  # proportional pause
                logger.debug(
                    "Reddit rate %.1f/min exceeds target %d/min, sleeping %.1fs",
                    current_rate, self.target_rate, sleep_time,
                )
                time.sleep(sleep_time)
