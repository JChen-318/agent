"""Path cache for known app UI patterns — avoids repeated LLM calls for common flows."""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Max entries in cache
MAX_ENTRIES = 200

# TTL per entry (7 days — app UIs don't change often)
ENTRY_TTL = 7 * 24 * 3600


class PathCache:
    """Caches known UI interaction paths keyed by (app, page, intent).

    Hit scenario: user says "在美团搜索火锅" → cache has the widget path
    from a previous "在美团搜索烧烤" execution → reuse the same click path.
    """

    def __init__(self, cache_file: Optional[Path] = None):
        self._cache: dict[str, dict] = {}
        self._cache_file = cache_file
        if cache_file and cache_file.exists():
            self._load()

    def _key(self, app: str, page: str, intent: str) -> str:
        raw = f"{app}|{page}|{intent}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def get(self, app: str, page: str, intent: str) -> Optional[list[dict]]:
        """Look up cached tool calls for this app/page/intent combination."""
        key = self._key(app, page, intent)
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry["_ts"] > ENTRY_TTL:
            del self._cache[key]
            return None
        logger.info(f"Path cache hit: {app}/{page} -> {len(entry['actions'])} steps")
        return entry["actions"]

    def set(self, app: str, page: str, intent: str, actions: list[dict]) -> None:
        """Cache successful tool call sequence for this context."""
        key = self._key(app, page, intent)
        self._cache[key] = {"actions": actions, "_ts": time.time()}
        if len(self._cache) > MAX_ENTRIES:
            oldest = min(self._cache, key=lambda k: self._cache[k]["_ts"])
            del self._cache[oldest]
        if self._cache_file:
            self._save()

    def get_by_app(self, app: str) -> list[dict]:
        """Get all cached entries for a specific app (for pre-warming)."""
        results = []
        for key, entry in self._cache.items():
            if time.time() - entry["_ts"] > ENTRY_TTL:
                continue
            results.append(entry)
        return results

    def _save(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception:
            logger.debug("Failed to save path cache", exc_info=True)

    def _load(self) -> None:
        try:
            with open(self._cache_file, encoding="utf-8") as f:
                self._cache = json.load(f)
            # Purge expired entries
            now = time.time()
            self._cache = {
                k: v for k, v in self._cache.items()
                if now - v.get("_ts", 0) < ENTRY_TTL
            }
            logger.info(f"Loaded {len(self._cache)} path cache entries")
        except Exception:
            logger.debug("Failed to load path cache", exc_info=True)
