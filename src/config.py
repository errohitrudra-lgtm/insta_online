"""
Configuration management for Instagram Reel Monitor.

Reads config.json.  Uses SeleniumBase for scraping and uploading,
yt-dlp for downloading, and optional NVIDIA AI for smart assistance.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _BASE_DIR / "config.json"


@dataclass
class TargetAccount:
    username: str
    min_views: int = 1000
    recheck_rejected_days: int = 10  # Re-check rejected reels after N days


@dataclass
class MyAccount:
    username: str = ""
    password: str = ""


@dataclass
class StorageSettings:
    local_dir: str = str(_BASE_DIR / "storage")


@dataclass
class RetrySettings:
    max_attempts: int = 5
    base_delay: float = 2.0
    max_delay: float = 120.0
    exponential_base: float = 2.0


@dataclass
class QueueSettings:
    backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    max_workers: int = 2


@dataclass
class WebSettings:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AISettings:
    """NVIDIA AI helper – optional, app works without it."""
    enabled: bool = False
    api_key: str = ""
    model: str = "mistralai/mistral-large-3-675b-instruct-2512"
    invoke_url: str = "https://integrate.api.nvidia.com/v1/chat/completions"


@dataclass
class UploadSettings:
    """Upload configuration."""
    enabled: bool = False
    auto_upload: bool = True           # auto-upload new reels as they are downloaded
    schedule_enabled: bool = True      # use scheduled upload times
    schedule_times: List[str] = field(default_factory=lambda: ["09:00", "13:00", "18:00"])
    reels_per_schedule: int = 3        # how many reels to upload per schedule slot
    delay_between_uploads: int = 1200  # seconds between consecutive uploads (20 min)
    headless: bool = True              # run browser headless for upload
    retry_failed: bool = True          # auto-retry failed uploads
    retry_max_attempts: int = 3        # max retry attempts for failed uploads
    retry_delay_minutes: int = 30      # minutes between retry attempts
    cleanup_after_upload: bool = False  # delete local file after successful upload
    cleanup_after_days: int = 7        # delete uploaded files older than N days (0=never)
    catchup_enabled: bool = True       # periodically upload any downloaded-but-not-uploaded reels
    catchup_interval_minutes: int = 60 # how often to check for missed reels (minutes)


@dataclass
class AppConfig:
    my_account: MyAccount = field(default_factory=MyAccount)
    targets: List[TargetAccount] = field(default_factory=list)

    poll_interval_seconds: int = 300
    max_reels_per_scan: int = 20

    storage: StorageSettings = field(default_factory=StorageSettings)
    retry: RetrySettings = field(default_factory=RetrySettings)
    queue: QueueSettings = field(default_factory=QueueSettings)
    web: WebSettings = field(default_factory=WebSettings)
    ai: AISettings = field(default_factory=AISettings)
    upload: UploadSettings = field(default_factory=UploadSettings)

    state_file: str = str(_BASE_DIR / "state.json")
    log_level: str = "INFO"
    log_file: str = str(_BASE_DIR / "logs" / "app.log")


def _build_target(data: Dict[str, Any]) -> TargetAccount:
    return TargetAccount(
        username=data.get("username", ""),
        min_views=data.get("min_views", 1000),
        recheck_rejected_days=data.get("recheck_rejected_days", 10),
    )


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    if path is None:
        path = os.environ.get("INSTA_CONFIG", str(DEFAULT_CONFIG_PATH))
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = json.load(fh)

    my_raw = raw.get("my_account", {})
    targets_raw = raw.get("targets", [])
    storage_raw = raw.get("storage", {})
    retry_raw = raw.get("retry", {})
    queue_raw = raw.get("queue", {})
    web_raw = raw.get("web", {})
    ai_raw = raw.get("ai", {})
    upload_raw = raw.get("upload", {})

    cfg = AppConfig(
        my_account=MyAccount(
            username=my_raw.get("username", ""),
            password=my_raw.get("password", ""),
        ),
        targets=[_build_target(t) for t in targets_raw],
        poll_interval_seconds=raw.get("poll_interval_seconds", 300),
        max_reels_per_scan=raw.get("max_reels_per_scan", 20),
        storage=StorageSettings(**{
            k: v for k, v in storage_raw.items()
            if k in StorageSettings.__dataclass_fields__
        }),
        retry=RetrySettings(**{
            k: v for k, v in retry_raw.items()
            if k in RetrySettings.__dataclass_fields__
        }),
        queue=QueueSettings(**{
            k: v for k, v in queue_raw.items()
            if k in QueueSettings.__dataclass_fields__
        }),
        web=WebSettings(**{
            k: v for k, v in web_raw.items()
            if k in WebSettings.__dataclass_fields__
        }),
        ai=AISettings(**{
            k: v for k, v in ai_raw.items()
            if k in AISettings.__dataclass_fields__
        }),
        upload=UploadSettings(**{
            k: v for k, v in upload_raw.items()
            if k in UploadSettings.__dataclass_fields__
        }),
        state_file=raw.get("state_file", str(_BASE_DIR / "state.json")),
        log_level=raw.get("log_level", "INFO"),
        log_file=raw.get("log_file", str(_BASE_DIR / "logs" / "app.log")),
    )
    return cfg
