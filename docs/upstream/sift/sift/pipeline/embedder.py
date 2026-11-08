import logging
import numpy as np
from typing import List
from sentence_transformers import SentenceTransformer
from sift.models import FeedbackItem
from sift.config import ClusteringConfig

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, config: ClusteringConfig):
        self.model = SentenceTransformer(config.embedding_model)
        self.config = config

    def embed(self, items: List[FeedbackItem]) -> np.ndarray:
        texts = [item.text for item in items]
        logger.info("Generating embeddings for %d feedback items using %s...", len(items), self.config.embedding_model)
        embeddings = self.model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        logger.debug("Generated embeddings matrix of shape %s", embeddings.shape)
        return embeddings

    def embed_profile(self, text: str) -> np.ndarray:
        """Embed a single string into a 1-D, L2-normalized 384-dim vector.

        Used for the product anchor (Layer 2). Normalized so cosine similarity
        against item embeddings (also normalized) is a plain dot product.
        """
        return self.model.encode(text, show_progress_bar=False, normalize_embeddings=True)
