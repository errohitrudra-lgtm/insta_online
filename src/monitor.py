"""
Background reel monitor.

Polls target accounts via SeleniumBase scraper and dispatches
new reel downloads. Monitors continuously for new content.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .config import AppConfig, TargetAccount
from .instagram_client import InstagramScraper, ReelInfo
from .logger import get_logger
from .state_manager import StateManager

log = get_logger("monitor")

NewReelCallback = Callable[[TargetAccount, ReelInfo], Coroutine[Any, Any, None]]


class ReelMonitor:
    def __init__(
        self,
        config: AppConfig,
        scraper: InstagramScraper,
        state: StateManager,
        on_new_reel: NewReelCallback,
    ) -> None:
        self.config = config
        self.scraper = scraper
        self.state = state
        self.on_new_reel = on_new_reel
        self._running = False
        self._tasks: List[asyncio.Task] = []

        self.force_refresh = asyncio.Event()
        self.stats: Dict[str, Dict[str, Any]] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        for target in self.config.targets:
            task = asyncio.create_task(
                self._poll_loop(target), name=f"monitor-{target.username}"
            )
            self._tasks.append(task)
            self.stats[target.username] = {
                "username": target.username,
                "min_views": target.min_views,
                "last_poll": None,
                "reels_found": 0,
                "new_reels": 0,
                "total_downloaded": 0,
                "errors": 0,
                "status": "starting",
            }
        log.info(
            "Monitor started for %d target(s): %s",
            len(self.config.targets),
            ", ".join(f"@{t.username}" for t in self.config.targets),
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("Monitor stopped")

    async def _poll_loop(self, target: TargetAccount) -> None:
        import random

        # On startup, check if we polled recently — skip if within interval
        last_poll = self.state.get_last_poll_time(target.username)
        if last_poll > 0:
            elapsed = time.time() - last_poll
            remaining = self.config.poll_interval_seconds - elapsed
            if remaining > 60:  # more than 1 min left
                jitter = random.randint(0, 1800)
                wait_time = int(remaining) + jitter
                log.info(
                    "⏭ Last poll for @%s was %d min ago – next poll in %d min %ds",
                    target.username, int(elapsed // 60),
                    wait_time // 60, wait_time % 60,
                )
                self.stats[target.username]["status"] = "idle – waiting (recently polled)"
                try:
                    await asyncio.wait_for(
                        self.force_refresh.wait(),
                        timeout=wait_time,
                    )
                    self.force_refresh.clear()
                    log.info("Force refresh triggered")
                except asyncio.TimeoutError:
                    pass

        while self._running:
            try:
                await self._poll_once(target)
                self.state.set_last_poll_time(target.username)
                self.stats[target.username]["status"] = "idle – waiting"
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.stats[target.username]["errors"] += 1
                self.stats[target.username]["status"] = f"error: {exc}"
                log.exception("Error polling @%s: %s", target.username, exc)

            # Wait poll_interval + random 0-30 min jitter for human-like timing
            jitter = random.randint(0, 1800)  # 0-30 minutes
            wait_time = self.config.poll_interval_seconds + jitter
            log.info(
                "Next poll for @%s in %d min %ds (interval %ds + jitter %ds)",
                target.username,
                wait_time // 60, wait_time % 60,
                self.config.poll_interval_seconds, jitter,
            )

            try:
                await asyncio.wait_for(
                    self.force_refresh.wait(),
                    timeout=wait_time,
                )
                self.force_refresh.clear()
                log.info("Force refresh triggered")
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self, target: TargetAccount) -> None:
        log.info("Polling @%s (min views: %d) …", target.username, target.min_views)
        self.stats[target.username]["last_poll"] = time.time()
        self.stats[target.username]["status"] = "polling"

        reels = await self.scraper.get_reels(
            target, max_count=self.config.max_reels_per_scan
        )
        self.stats[target.username]["reels_found"] = len(reels)

        new_count = 0
        skipped_rejected = 0
        for reel in reels:
            if self.state.is_reel_processed(target.username, reel.shortcode):
                continue

            # Skip reels that were recently rejected (below min_views)
            # They'll be re-checked after the cooldown period expires
            if self.state.is_reel_rejected(
                target.username, reel.shortcode,
                recheck_days=target.recheck_rejected_days,
            ):
                skipped_rejected += 1
                continue

            # Note: grid view counts are unreliable – real filtering
            # happens via yt-dlp metadata in the download worker.
            log.info(
                "🆕 New reel: %s from @%s (grid hint: %d views)",
                reel.shortcode, target.username, reel.view_count,
            )
            new_count += 1

            try:
                await self.on_new_reel(target, reel)
            except Exception as exc:
                log.exception("Callback failed for reel %s: %s", reel.shortcode, exc)

        self.stats[target.username]["new_reels"] += new_count
        if skipped_rejected:
            log.info(
                "Skipped %d recently-rejected reels (recheck in %d days)",
                skipped_rejected, target.recheck_rejected_days,
            )
        log.info(
            "Poll done for @%s – %d qualifying, %d new",
            target.username, len(reels), new_count,
        )
