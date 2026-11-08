import os
import tempfile


def ensure_numba_cache_dir() -> str:
    """Give numba a writable cache location before UMAP imports."""
    cache_dir = os.environ.get("NUMBA_CACHE_DIR")
    if not cache_dir:
        cache_dir = os.path.join(tempfile.gettempdir(), "sift-numba-cache")
        os.environ["NUMBA_CACHE_DIR"] = cache_dir

    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir
