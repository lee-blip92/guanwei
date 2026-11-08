"""SQLite-backed cache for ProductProfile and SourceRef resolutions.

Two tables:

* ``profiles(name_key, profile_json, resolved_at, resolver_used)``
* ``refs(source, profile_key, ref_json, resolved_at, resolver_used,
  confidence)``

Both tables carry provenance columns so a future debugger can answer
"which backend resolved this, and when?" without re-running the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sift.pipeline.resolver.models import CachedEntry, ProductProfile, SourceRef

logger = logging.getLogger(__name__)


def _name_key(name: str) -> str:
    return hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()


def _profile_key(profile: ProductProfile) -> str:
    # Profile identity is the homepage (most stable) or name (fallback).
    seed = (profile.homepage or profile.name).strip().lower()
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


class ResolverCache:
    def __init__(self, path: str, ttl_days: int = 30):
        self.path = os.path.expanduser(path)
        self.ttl = timedelta(days=ttl_days)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    name_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    resolver_used TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS refs (
                    source TEXT NOT NULL,
                    profile_key TEXT NOT NULL,
                    ref_json TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    resolver_used TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY (source, profile_key)
                );
                """
            )

    # -- Profile API ----------------------------------------------------

    def get_profile(self, name: str) -> Optional[CachedEntry]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_json, resolved_at, resolver_used FROM profiles WHERE name_key = ?",
                (_name_key(name),),
            ).fetchone()
        if not row:
            return None
        resolved_at = datetime.fromisoformat(row["resolved_at"])
        if self._is_stale(resolved_at):
            return None
        profile = ProductProfile.from_dict(json.loads(row["profile_json"]))
        return CachedEntry(value=profile, resolved_at=resolved_at, resolver_used=row["resolver_used"])

    def put_profile(self, profile: ProductProfile) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles (name_key, name, profile_json, resolved_at, resolver_used)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name_key) DO UPDATE SET
                    profile_json=excluded.profile_json,
                    resolved_at=excluded.resolved_at,
                    resolver_used=excluded.resolver_used
                """,
                (
                    _name_key(profile.name),
                    profile.name,
                    json.dumps(profile.to_dict()),
                    now,
                    profile.enricher_used,
                ),
            )

    # -- Ref API --------------------------------------------------------

    def get_ref(self, source: str, profile: ProductProfile) -> Optional[CachedEntry]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ref_json, resolved_at, resolver_used FROM refs WHERE source = ? AND profile_key = ?",
                (source, _profile_key(profile)),
            ).fetchone()
        if not row:
            return None
        resolved_at = datetime.fromisoformat(row["resolved_at"])
        if self._is_stale(resolved_at):
            return None
        ref = SourceRef.from_dict(json.loads(row["ref_json"]))
        return CachedEntry(value=ref, resolved_at=resolved_at, resolver_used=row["resolver_used"])

    def put_ref(self, profile: ProductProfile, ref: SourceRef) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO refs (source, profile_key, ref_json, resolved_at, resolver_used, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, profile_key) DO UPDATE SET
                    ref_json=excluded.ref_json,
                    resolved_at=excluded.resolved_at,
                    resolver_used=excluded.resolver_used,
                    confidence=excluded.confidence
                """,
                (
                    ref.source,
                    _profile_key(profile),
                    json.dumps(ref.to_dict()),
                    now,
                    ref.resolver_used,
                    ref.confidence,
                ),
            )

    # -- Inspection / invalidation -------------------------------------

    def list_profiles(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, resolved_at, resolver_used FROM profiles ORDER BY resolved_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_refs_for(self, name: str) -> list[dict]:
        # TTL-bypass: list refs even when the profile is stale, so a user
        # inspecting the cache after expiry can still see what's stored.
        profile = self._load_profile_raw(name)
        if profile is None:
            return []
        profile_key = _profile_key(profile)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source, ref_json, resolved_at, resolver_used, confidence FROM refs WHERE profile_key = ?",
                (profile_key,),
            ).fetchall()
        return [
            {
                "source": r["source"],
                "ref": SourceRef.from_dict(json.loads(r["ref_json"])),
                "resolved_at": r["resolved_at"],
                "resolver_used": r["resolver_used"],
                "confidence": r["confidence"],
            }
            for r in rows
        ]

    def clear_profile(self, name: str) -> int:
        """Delete a profile and all refs derived from it. Returns rows removed."""
        # TTL-bypass: cleanup must work on stale profiles too, otherwise
        # orphaned ref rows accumulate and `sift cache clear` silently
        # fails to remove them.
        profile = self._load_profile_raw(name)
        with self._connect() as conn:
            count = conn.execute(
                "DELETE FROM profiles WHERE name_key = ?", (_name_key(name),)
            ).rowcount
            if profile is not None:
                count += conn.execute(
                    "DELETE FROM refs WHERE profile_key = ?", (_profile_key(profile),)
                ).rowcount
        return count

    def clear_ref(self, name: str, source: str) -> int:
        # TTL-bypass: same reasoning as clear_profile.
        profile = self._load_profile_raw(name)
        if profile is None:
            return 0
        with self._connect() as conn:
            return conn.execute(
                "DELETE FROM refs WHERE source = ? AND profile_key = ?",
                (source, _profile_key(profile)),
            ).rowcount

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.executescript("DELETE FROM refs; DELETE FROM profiles;")

    # -- Internal ------------------------------------------------------

    def _is_stale(self, resolved_at: datetime) -> bool:
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - resolved_at > self.ttl

    def _load_profile_raw(self, name: str) -> Optional[ProductProfile]:
        """Load a profile from disk ignoring the TTL.

        Used by cleanup / inspection paths so that an expired profile row
        can still be located (and its ``profile_key`` recomputed to find
        derived refs) after ``get_profile`` would refuse to return it.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_json FROM profiles WHERE name_key = ?",
                (_name_key(name),),
            ).fetchone()
        if not row:
            return None
        return ProductProfile.from_dict(json.loads(row["profile_json"]))
