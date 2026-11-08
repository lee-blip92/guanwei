import logging
import numpy as np
from typing import List, Dict
from sift.pipeline.numba_cache import ensure_numba_cache_dir

ensure_numba_cache_dir()

from umap import UMAP
import hdbscan
from sift.models import ClusterResult, FeedbackItem
from sift.config import ClusteringConfig

logger = logging.getLogger(__name__)


class Clusterer:
    def __init__(self, config: ClusteringConfig):
        self.config = config
        self.umap = UMAP(
            n_neighbors=config.umap_n_neighbors,
            n_components=config.umap_n_components,
            metric="cosine",
            random_state=42,
        )
        self.hdbscan = hdbscan.HDBSCAN(
            min_cluster_size=config.hdbscan_min_cluster_size,
            min_samples=config.hdbscan_min_samples,
            metric="euclidean",
            cluster_selection_method="eom",
        )

    def cluster(self, embeddings: np.ndarray, items: List[FeedbackItem]) -> List[ClusterResult]:
        if len(items) < 3:
            logger.warning("Too few items to cluster (%d), returning single group.", len(items))
            return [ClusterResult(cluster_id=0, items=items)]

        reduced = self.umap.fit_transform(embeddings)
        labels = self.hdbscan.fit_predict(reduced)

        clusters: Dict[int, ClusterResult] = {}
        for idx, label in enumerate(labels):
            if label not in clusters:
                clusters[label] = ClusterResult(cluster_id=int(label))
            clusters[label].items.append(items[idx])

        # Pick representative quotes (longest diverse texts) for each cluster
        for cluster in clusters.values():
            if cluster.is_noise:
                continue
            sorted_items = sorted(cluster.items, key=lambda x: len(x.text), reverse=True)
            cluster.representative_quotes = [
                item.text[:200] for item in sorted_items[:3]
            ]

        # Filter out noise cluster if it exists, keep it but flag it
        result = sorted(
            [c for c in clusters.values() if not c.is_noise],
            key=lambda c: c.size,
            reverse=True,
        )
        noise = clusters.get(-1)
        if noise and noise.size > 0:
            logger.debug("%d items classified as noise (no clear theme).", noise.size)

        logger.info("Found %d clusters from %d items.", len(result), len(items))
        return result
