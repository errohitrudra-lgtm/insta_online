"""
Persistent reel tracking database.

Stores all discovered reels in a JSON file with their status
(discovered, downloaded, uploaded), caption, hashtags, file path, etc.
Survives app restarts.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import get_logger

log = get_logger("reels_db")

_DB_PATH = Path("./reels_db.json")


@dataclass
class ReelRecord:
    """One row in the reels tracking table."""

    shortcode: str
    username: str = ""
    permalink: str = ""
    caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    view_count: int = 0
    like_count: int = 0
    discovered_at: float = field(default_factory=time.time)
    downloaded: bool = False
    downloaded_at: float = 0.0
    local_path: str = ""
    uploaded: bool = False
    uploaded_at: float = 0.0
    upload_caption: str = ""  # caption used for upload (may differ from original)
    error: str = ""


class ReelsDatabase:
    """Thread-safe JSON-backed reel tracking store."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path else _DB_PATH
        self._lock = threading.Lock()
        self._records: Dict[str, ReelRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for key, val in raw.items():
                    self._records[key] = ReelRecord(**{
                        k: v for k, v in val.items()
                        if k in ReelRecord.__dataclass_fields__
                    })
                log.info("Loaded %d reels from %s", len(self._records), self._path.name)
            except Exception as exc:
                log.warning("Failed to load reels DB: %s", exc)
        else:
            log.info("No existing reels DB – starting fresh")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {k: asdict(v) for k, v in self._records.items()},
                fh,
                indent=2,
                ensure_ascii=False,
            )
        tmp.replace(self._path)

    # ---- Public API ----

    def add_reel(
        self,
        shortcode: str,
        username: str = "",
        caption: str = "",
        hashtags: List[str] | None = None,
        view_count: int = 0,
        like_count: int = 0,
    ) -> bool:
        """Add a reel if not already tracked. Returns True if new."""
        with self._lock:
            if shortcode in self._records:
                return False
            self._records[shortcode] = ReelRecord(
                shortcode=shortcode,
                username=username,
                permalink=f"https://www.instagram.com/reel/{shortcode}/",
                caption=caption,
                hashtags=hashtags or [],
                view_count=view_count,
                like_count=like_count,
            )
            self._save()
            return True

    def get(self, shortcode: str) -> Optional[ReelRecord]:
        with self._lock:
            return self._records.get(shortcode)

    def mark_downloaded(
        self,
        shortcode: str,
        local_path: str,
        caption: str = "",
        hashtags: List[str] | None = None,
    ) -> None:
        with self._lock:
            rec = self._records.get(shortcode)
            if rec:
                rec.downloaded = True
                rec.downloaded_at = time.time()
                rec.local_path = local_path
                if caption:
                    rec.caption = caption
                if hashtags:
                    rec.hashtags = hashtags
                self._save()

    def mark_uploaded(self, shortcode: str, upload_caption: str = "") -> None:
        with self._lock:
            rec = self._records.get(shortcode)
            if rec:
                rec.uploaded = True
                rec.uploaded_at = time.time()
                rec.upload_caption = upload_caption
                self._save()

    def mark_error(self, shortcode: str, error: str) -> None:
        with self._lock:
            rec = self._records.get(shortcode)
            if rec:
                rec.error = error
                self._save()

    def is_downloaded(self, shortcode: str) -> bool:
        with self._lock:
            rec = self._records.get(shortcode)
            return rec.downloaded if rec else False

    def is_uploaded(self, shortcode: str) -> bool:
        with self._lock:
            rec = self._records.get(shortcode)
            return rec.uploaded if rec else False

    def get_not_downloaded(self) -> List[ReelRecord]:
        with self._lock:
            return [r for r in self._records.values() if not r.downloaded]

    def get_downloaded_not_uploaded(self) -> List[ReelRecord]:
        with self._lock:
            return [
                r for r in self._records.values()
                if r.downloaded and not r.uploaded and r.local_path
            ]

    def get_all(self) -> List[ReelRecord]:
        with self._lock:
            return list(self._records.values())

    @property
    def total(self) -> int:
        return len(self._records)

    @property
    def downloaded_count(self) -> int:
        return sum(1 for r in self._records.values() if r.downloaded)

    @property
    def uploaded_count(self) -> int:
        return sum(1 for r in self._records.values() if r.uploaded)

    def update_caption(self, shortcode: str, caption: str, hashtags: List[str] | None = None) -> None:
        """Update caption and hashtags (e.g., after yt-dlp extracts them)."""
        with self._lock:
            rec = self._records.get(shortcode)
            if rec:
                rec.caption = caption
                if hashtags is not None:
                    rec.hashtags = hashtags
                self._save()

    def clear_error(self, shortcode: str) -> None:
        """Clear error field (e.g., after successful re-download)."""
        with self._lock:
            rec = self._records.get(shortcode)
            if rec:
                rec.error = ""
                self._save()
