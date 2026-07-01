"""
Main orchestrator – wires everything together.

Lifecycle:
  1. Load config, set up logging
  2. Init scraper, state, downloader, uploader, queue, monitor, AI helper
  3. Start queue workers + monitor + web dashboard + upload scheduler
  4. Run 24/7 until Ctrl-C
"""

from __future__ import annotations

import asyncio
import signal
import time
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Optional

import uvicorn  # type: ignore

from .ai_helper import AIHelper
from .config import AppConfig, TargetAccount, load_config
from .downloader import ReelDownloader
from .instagram_client import InstagramScraper, ReelInfo
from .logger import get_logger, setup_logging
from .monitor import ReelMonitor
from .queue_manager import Job, JobStatus, create_queue
from .reels_db import ReelsDatabase
from .state_manager import StateManager
from .uploader import ReelUploader

log = get_logger("main")


class Application:
    """Top-level application container."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = StateManager(config.state_file)
        self.reels_db = ReelsDatabase()
        self.scraper = InstagramScraper(
            retry=config.retry,
            login_user=config.my_account.username,
            login_pass=config.my_account.password,
            headless=config.upload.headless,
            ai_settings=config.ai,
        )
        self.downloader = ReelDownloader(config.storage, login_user=config.my_account.username)
        self.uploader = ReelUploader(config)
        self.ai = AIHelper(config.ai)
        self.queue = create_queue(
            backend=config.queue.backend,
            redis_url=config.queue.redis_url,
            max_workers=config.queue.max_workers,
        )
        self.monitor = ReelMonitor(
            config=config,
            scraper=self.scraper,
            state=self.state,
            on_new_reel=self._on_new_reel,
        )

        # Upload timing: track last upload time for 20min+ gap enforcement
        self._last_upload_time: float = 0.0

        # Batch upload status tracking
        self._batch_status: dict = {
            "active": False,
            "total": 0,
            "uploaded": 0,
            "current": "",
            "shortcodes": [],
            "waiting_until": 0.0,
        }

        # Failed upload retry tracking: shortcode -> attempt count
        self._retry_counts: Dict[str, int] = {}

        # Daily stats
        self._stats: Dict[str, int] = {
            "downloads_today": 0,
            "uploads_today": 0,
            "upload_failures_today": 0,
            "retries_today": 0,
        }
        self._stats_date: str = date.today().isoformat()

        # Reconcile DB with actual storage on startup
        self._reconcile_db()

    def _reconcile_db(self) -> None:
        """Scan storage folder and fix stale DB entries on startup.

        If a file exists in storage but the DB says it's not downloaded
        or has an error, fix the DB entry. This prevents re-downloading
        reels that were already saved in a previous run.
        """
        storage = Path(self.config.storage.local_dir)
        if not storage.exists():
            return

        fixed = 0
        for mp4 in storage.glob("*.mp4"):
            if mp4.stat().st_size == 0:
                continue
            # Extract shortcode from filename: username_SHORTCODE.mp4
            parts = mp4.stem.rsplit("_", 1)
            if len(parts) != 2:
                continue
            shortcode = parts[1]

            rec = self.reels_db.get(shortcode)
            if rec is None:
                # File exists but not in DB — add it
                username = parts[0]
                self.reels_db.add_reel(shortcode=shortcode, username=username)
                self.reels_db.mark_downloaded(shortcode, local_path=str(mp4))
                fixed += 1
            elif not rec.downloaded or rec.error:
                # File exists but DB says not downloaded or has error — fix it
                self.reels_db.mark_downloaded(shortcode, local_path=str(mp4))
                if rec.error:
                    self.reels_db.clear_error(shortcode)
                fixed += 1

            # Also mark as processed in state so monitor skips it
            username = parts[0]
            self.state.set_last_reel_id(username, shortcode, 0)

        if fixed:
            log.info("🔧 Reconciled %d reels from storage (fixed stale DB entries)", fixed)

    def _find_target(self, username: str) -> Optional[TargetAccount]:
        """Look up the TargetAccount config for a given username."""
        for t in self.config.targets:
            if t.username == username:
                return t
        return None

    async def _on_new_reel(self, target: TargetAccount, reel: ReelInfo) -> None:
        # ── Duplicate guard: skip if already in tracking DB ──
        if self.reels_db.is_downloaded(reel.shortcode):
            log.debug("Skipping reel %s – already downloaded (DB)", reel.shortcode)
            return

        # ── Duplicate guard: skip if already in queue ──
        for existing in self.queue.get_all_jobs():
            if existing.reel_shortcode == reel.shortcode:
                log.debug("Skipping duplicate reel %s (already in queue)", reel.shortcode)
                return

        # ── Duplicate guard: skip if file already downloaded ──
        safe_user = reel.username or target.username
        expected_file = Path(self.config.storage.local_dir) / f"{safe_user}_{reel.shortcode}.mp4"
        if expected_file.exists() and expected_file.stat().st_size > 0:
            log.info("Skipping reel %s – already downloaded at %s", reel.shortcode, expected_file.name)
            # Ensure DB is consistent
            if not self.reels_db.is_downloaded(reel.shortcode):
                self.reels_db.add_reel(shortcode=reel.shortcode, username=target.username)
                self.reels_db.mark_downloaded(reel.shortcode, local_path=str(expected_file))
            self.state.set_last_reel_id(target.username, reel.shortcode, reel.timestamp)
            return

        # Add to tracking DB
        self.reels_db.add_reel(
            shortcode=reel.shortcode,
            username=target.username,
            caption=reel.caption,
            hashtags=reel.hashtags,
            view_count=reel.view_count,
            like_count=reel.like_count,
        )

        job = Job(
            target_username=target.username,
            reel_shortcode=reel.shortcode,
            video_url=reel.video_url,
            caption=reel.caption,
            view_count=reel.view_count,
            like_count=reel.like_count,
            permalink=f"https://www.instagram.com/reel/{reel.shortcode}/",
        )
        await self.queue.enqueue(job)

    async def _process_job(self, job: Job) -> None:
        """Worker: download → check real view count → optionally auto-upload."""
        job.attempts += 1
        job.updated_at = time.time()
        job.status = JobStatus.DOWNLOADING

        reel = ReelInfo(
            shortcode=job.reel_shortcode,
            video_url=job.video_url,
            caption=job.caption,
            view_count=job.view_count,
            like_count=job.like_count,
            permalink=job.permalink,
            username=job.target_username,
        )

        target = self._find_target(job.target_username)
        min_views = target.min_views if target else 0

        log.info("Job %s: downloading reel %s …", job.id, job.reel_shortcode)

        try:
            file_path, caption, hashtags, real_views = await self.downloader.download(reel)
        except Exception as exc:
            # Ask AI for help if available
            ai_advice = await self.ai.analyse_error(str(exc), f"Downloading reel {job.reel_shortcode}")
            if ai_advice:
                log.info("AI suggestion: %s", ai_advice[:200])
            self.reels_db.mark_error(job.reel_shortcode, str(exc))
            raise

        # ── Post-download view count filter ──
        # yt-dlp gives us the REAL view count during download.
        # If it's below min_views, delete the file and skip.
        if real_views > 0:
            job.view_count = real_views
            reel.view_count = real_views

        if min_views > 0 and real_views > 0 and real_views < min_views:
            recheck_days = target.recheck_rejected_days if target else 10
            log.info(
                "Job %s: ⏭ Reel %s has %d views (< %d min_views) – deleting & skipping (recheck in %d days)",
                job.id, job.reel_shortcode, real_views, min_views, recheck_days,
            )
            # Delete the downloaded file
            try:
                Path(file_path).unlink(missing_ok=True)
                log.info("Job %s: deleted %s", job.id, Path(file_path).name)
            except Exception as del_exc:
                log.warning("Job %s: could not delete file: %s", job.id, del_exc)
            job.status = JobStatus.FAILED
            job.error = f"Below min_views: {real_views} < {min_views}"
            job.updated_at = time.time()
            # Mark as rejected with a cooldown — will be re-checked after recheck_days
            self.state.mark_reel_rejected(job.target_username, job.reel_shortcode, real_views)
            return

        if min_views > 0 and real_views == 0:
            log.warning(
                "Job %s: yt-dlp returned no view count for %s – keeping file (can't verify)",
                job.id, job.reel_shortcode,
            )

        if real_views > 0:
            log.info(
                "Job %s: ✅ reel %s has %d views (>= %d min_views) – keeping!",
                job.id, job.reel_shortcode, real_views, min_views,
            )
            # Clear any previous rejection if the reel now qualifies
            self.state.clear_rejected_reel(job.target_username, job.reel_shortcode)

        # Update job and DB with extracted caption/hashtags
        if caption:
            job.caption = caption
        if hashtags:
            pass  # stored in DB

        job.local_path = file_path
        job.status = JobStatus.DOWNLOADED
        job.updated_at = time.time()

        # Update daily stats
        self._reset_daily_stats_if_needed()
        self._stats["downloads_today"] += 1

        # Update tracking DB
        self.reels_db.mark_downloaded(
            job.reel_shortcode,
            local_path=file_path,
            caption=caption,
            hashtags=hashtags,
        )

        self.state.set_last_reel_id(job.target_username, job.reel_shortcode, reel.timestamp)
        log.info("Job %s: ✅ downloaded → %s", job.id, file_path)
        if caption:
            log.info("Job %s: Caption: %.120s", job.id, caption)

        # -- Auto-upload if enabled --
        if self.config.upload.auto_upload and self.uploader.is_enabled:
            await self._do_upload(job)

    async def _do_upload(self, job: Job) -> None:
        """Upload a downloaded reel."""
        if not job.local_path:
            log.warning("Job %s: no local file to upload", job.id)
            return

        # ── Duplicate upload guard ──
        if job.status == JobStatus.UPLOADED:
            log.info("Job %s: already uploaded, skipping", job.id)
            return

        # Check tracking DB
        if self.reels_db.is_uploaded(job.reel_shortcode):
            log.info("Job %s: reel %s already uploaded (DB), skipping",
                     job.id, job.reel_shortcode)
            job.status = JobStatus.UPLOADED
            job.upload_result = "duplicate – already uploaded"
            return

        # Check if another job with the same shortcode was already uploaded
        for other in self.queue.get_all_jobs():
            if (other.id != job.id
                    and other.reel_shortcode == job.reel_shortcode
                    and other.status == JobStatus.UPLOADED):
                log.info("Job %s: reel %s already uploaded by job %s, skipping",
                         job.id, job.reel_shortcode, other.id)
                job.status = JobStatus.UPLOADED
                job.upload_result = "duplicate – already uploaded"
                return

        job.status = JobStatus.UPLOADING
        job.updated_at = time.time()
        log.info("Job %s: uploading %s …", job.id, Path(job.local_path).name)

        # Use exact original caption from DB (with all hashtags)
        db_record = self.reels_db.get(job.reel_shortcode)
        if db_record and db_record.caption:
            caption = db_record.caption
        else:
            caption = job.caption

        # Optionally rephrase caption via AI
        if self.ai.settings.enabled:
            try:
                rephrased = await self.ai.generate_caption(caption)
                if rephrased and rephrased != caption:
                    log.info("AI rephrased caption: %.80s…", rephrased)
                    caption = rephrased
            except Exception:
                pass

        result = await self.uploader.upload(job.local_path, caption)
        job.upload_result = result
        job.updated_at = time.time()
        self._reset_daily_stats_if_needed()

        if result.startswith("error"):
            job.status = JobStatus.FAILED
            job.error = result
            self._stats["upload_failures_today"] += 1
            log.error("Job %s: upload failed – %s", job.id, result)

            ai_advice = await self.ai.analyse_error(result, "Uploading reel via SeleniumBase")
            if ai_advice:
                log.info("AI suggestion: %s", ai_advice[:200])
        elif result == "uncertain":
            job.status = JobStatus.FAILED
            job.error = "Upload uncertain — no confirmation from Instagram"
            self._stats["upload_failures_today"] += 1
            log.warning("Job %s: ⚠ upload uncertain — please verify manually", job.id)
            self.reels_db.mark_error(job.reel_shortcode,
                                     "uncertain — no Instagram confirmation")
        else:
            job.status = JobStatus.UPLOADED
            self._stats["uploads_today"] += 1
            log.info("Job %s: ✅ uploaded successfully", job.id)
            self.reels_db.mark_uploaded(job.reel_shortcode, upload_caption=caption)
            # Cleanup file immediately if configured
            if self.config.upload.cleanup_after_upload and job.local_path:
                try:
                    Path(job.local_path).unlink(missing_ok=True)
                    log.info("Job %s: 🗑 cleaned up local file", job.id)
                except Exception:
                    pass

    async def upload_single_job(self, job_id: str) -> str:
        """Manually trigger upload for a specific job (called from web UI)."""
        job = self.queue.get_job(job_id)
        if not job:
            return "Job not found"
        if not job.local_path:
            return "No file downloaded yet"
        if not self.uploader.is_enabled:
            return "Upload is not enabled – set upload.enabled=true in config"

        await self._do_upload(job)
        return job.upload_result

    async def upload_all_pending(self, max_count: int = 0) -> int:
        """Upload downloaded-but-not-uploaded reels. Returns count.
        
        Args:
            max_count: Max reels to upload (0 = use config reels_per_schedule).
        """
        limit = max_count if max_count > 0 else self.config.upload.reels_per_schedule

        # Prefer reels from the DB (persistent) over job queue
        pending_db = self.reels_db.get_downloaded_not_uploaded()
        if not pending_db:
            # Fallback to job queue
            jobs = self.queue.get_downloaded_not_uploaded()
            if not jobs:
                return 0
            count = 0
            for job in jobs[:limit]:
                if self.uploader.is_enabled:
                    await self._wait_upload_gap()
                    await self._do_upload(job)
                    self._last_upload_time = time.time()
                    count += 1
            return count

        count = 0
        for rec in pending_db[:limit]:
            if not self.uploader.is_enabled:
                break
            await self._wait_upload_gap()

            caption = rec.caption or ""
            if self.ai.settings.enabled:
                try:
                    rephrased = await self.ai.generate_caption(caption)
                    if rephrased and rephrased != caption:
                        log.info("AI rephrased caption: %.80s…", rephrased)
                        caption = rephrased
                except Exception:
                    pass

            log.info("Scheduled upload: reel %s → %s", rec.shortcode, rec.local_path)
            result = await self.uploader.upload(rec.local_path, caption)
            self._last_upload_time = time.time()

            if result.startswith("error"):
                self.reels_db.mark_error(rec.shortcode, result)
                log.error("Scheduled upload failed for %s: %s", rec.shortcode, result)
            elif result == "uncertain":
                self.reels_db.mark_error(rec.shortcode, "uncertain — no Instagram confirmation")
                log.warning("⚠ Scheduled: reel %s upload uncertain", rec.shortcode)
            else:
                self.reels_db.mark_uploaded(rec.shortcode, upload_caption=caption)
                count += 1
                log.info("✅ Scheduled upload: reel %s uploaded (%d/%d)", rec.shortcode, count, limit)

        return count

    async def upload_reel_by_shortcode(self, shortcode: str) -> str:
        """Upload a single reel by its shortcode (from reels DB, not job queue)."""
        if not self.uploader.is_enabled:
            return "Upload not enabled – set upload.enabled=true in config"

        rec = self.reels_db.get(shortcode)
        if not rec:
            return f"Reel {shortcode} not found in DB"
        if not rec.downloaded or not rec.local_path:
            return f"Reel {shortcode} not downloaded yet"
        if rec.uploaded:
            return f"Reel {shortcode} already uploaded"

        # Manual upload — go immediately, no gap wait
        caption = rec.caption or ""
        if self.ai.settings.enabled:
            try:
                rephrased = await self.ai.generate_caption(caption)
                if rephrased and rephrased != caption:
                    caption = rephrased
            except Exception:
                pass

        log.info("Uploading reel %s → %s", shortcode, rec.local_path)
        result = await self.uploader.upload(rec.local_path, caption)
        self._last_upload_time = time.time()

        if result.startswith("error"):
            self.reels_db.mark_error(shortcode, result)
            return f"Upload failed: {result}"
        elif result == "uncertain":
            self.reels_db.mark_error(shortcode, "uncertain — no Instagram confirmation")
            log.warning("⚠ Reel %s upload uncertain — verify manually", shortcode)
            return "uncertain — check your Instagram account"
        else:
            self.reels_db.mark_uploaded(shortcode, upload_caption=caption)
            log.info("✅ Reel %s uploaded successfully", shortcode)
            return "success"

    def get_batch_status(self) -> dict:
        """Return current batch upload progress for the web UI."""
        bs = self._batch_status.copy()
        if bs["active"] and bs["waiting_until"] > 0:
            remaining = max(0, int(bs["waiting_until"] - time.time()))
            bs["wait_remaining_sec"] = remaining
            bs["wait_remaining_str"] = f"{remaining // 60}m {remaining % 60}s"
        else:
            bs["wait_remaining_sec"] = 0
            bs["wait_remaining_str"] = ""
        return bs

    async def upload_selected_reels(self, shortcodes: list) -> None:
        """Upload multiple reels from UI. First one goes immediately,
        then 20+ min gap between subsequent uploads."""
        self._batch_status = {
            "active": True,
            "total": len(shortcodes),
            "uploaded": 0,
            "current": "",
            "shortcodes": shortcodes,
            "waiting_until": 0.0,
        }
        log.info("📤 Batch upload started: %d reels", len(shortcodes))
        uploaded = 0
        for i, sc in enumerate(shortcodes):
            rec = self.reels_db.get(sc)
            if not rec or not rec.downloaded or not rec.local_path or rec.uploaded:
                log.info("Skipping %s (not ready or already uploaded)", sc)
                continue

            # First reel uploads immediately; subsequent ones wait 20+ min
            if uploaded > 0:
                self._batch_status["current"] = f"waiting before {sc}"
                await self._wait_upload_gap()

            self._batch_status["current"] = sc
            self._batch_status["waiting_until"] = 0.0

            caption = rec.caption or ""
            if self.ai.settings.enabled:
                try:
                    rephrased = await self.ai.generate_caption(caption)
                    if rephrased and rephrased != caption:
                        caption = rephrased
                except Exception:
                    pass

            log.info("Batch upload [%d/%d]: reel %s", i + 1, len(shortcodes), sc)
            result = await self.uploader.upload(rec.local_path, caption)
            self._last_upload_time = time.time()

            if result.startswith("error"):
                self.reels_db.mark_error(sc, result)
                log.error("Batch upload failed for %s: %s", sc, result)
            elif result == "uncertain":
                self.reels_db.mark_error(sc, "uncertain — no Instagram confirmation")
                log.warning("⚠ Batch: reel %s upload uncertain — verify manually", sc)
            else:
                self.reels_db.mark_uploaded(sc, upload_caption=caption)
                uploaded += 1
                self._batch_status["uploaded"] = uploaded
                log.info("✅ Batch upload: reel %s uploaded (%d/%d done)", sc, uploaded, len(shortcodes))

        self._batch_status["active"] = False
        self._batch_status["current"] = ""
        log.info("📤 Batch upload complete: %d/%d uploaded", uploaded, len(shortcodes))

    async def _wait_upload_gap(self) -> None:
        """Wait until at least 20 minutes + random jitter have passed since last upload."""
        import random
        min_gap = self.config.upload.delay_between_uploads  # 1200s = 20 min
        jitter = random.randint(60, 300)  # 1-5 min extra
        required_gap = min_gap + jitter

        elapsed = time.time() - self._last_upload_time
        if elapsed < required_gap:
            wait = int(required_gap - elapsed)
            self._batch_status["waiting_until"] = time.time() + wait
            log.info("⏳ Waiting %d min %ds before next upload (20+ min gap)…",
                     wait // 60, wait % 60)
            await asyncio.sleep(wait)
            self._batch_status["waiting_until"] = 0.0

    # ------------------------------------------------------------------
    # Stats helpers
    # ------------------------------------------------------------------

    def _reset_daily_stats_if_needed(self) -> None:
        """Reset daily stats at midnight."""
        today = date.today().isoformat()
        if today != self._stats_date:
            log.info("📊 Daily stats for %s: %s", self._stats_date, self._stats)
            self._stats = {
                "downloads_today": 0,
                "uploads_today": 0,
                "upload_failures_today": 0,
                "retries_today": 0,
            }
            self._stats_date = today

    def get_stats(self) -> dict:
        """Return current daily stats for the web dashboard."""
        self._reset_daily_stats_if_needed()
        return {**self._stats, "date": self._stats_date}

    # ------------------------------------------------------------------
    # Failed upload auto-retry loop
    # ------------------------------------------------------------------

    async def _retry_failed_uploads(self) -> None:
        """Background task: periodically retry failed uploads."""
        if not self.config.upload.retry_failed:
            log.info("Upload retry is disabled")
            return

        max_retries = self.config.upload.retry_max_attempts
        delay_min = self.config.upload.retry_delay_minutes
        log.info("Upload retry enabled: max %d attempts, %d min between retries", max_retries, delay_min)

        while True:
            try:
                await asyncio.sleep(delay_min * 60)
                self._reset_daily_stats_if_needed()

                if not self.uploader.is_enabled:
                    continue

                # Find reels that failed upload but are still downloaded locally
                all_reels = self.reels_db.get_all()
                retryable = [
                    r for r in all_reels
                    if r.downloaded and not r.uploaded and r.error
                    and r.local_path and Path(r.local_path).exists()
                    and self._retry_counts.get(r.shortcode, 0) < max_retries
                ]

                if not retryable:
                    continue

                log.info("🔄 Retrying %d failed uploads…", len(retryable))
                for rec in retryable:
                    attempt = self._retry_counts.get(rec.shortcode, 0) + 1
                    self._retry_counts[rec.shortcode] = attempt
                    self._stats["retries_today"] += 1

                    log.info("🔄 Retry %d/%d for reel %s (prev error: %s)",
                             attempt, max_retries, rec.shortcode, rec.error[:80])

                    # Clear the error so it can be retried
                    self.reels_db.clear_error(rec.shortcode)

                    await self._wait_upload_gap()

                    caption = rec.caption or ""
                    if self.ai.settings.enabled:
                        try:
                            rephrased = await self.ai.generate_caption(caption)
                            if rephrased and rephrased != caption:
                                caption = rephrased
                        except Exception:
                            pass

                    result = await self.uploader.upload(rec.local_path, caption)
                    self._last_upload_time = time.time()

                    if result.startswith("error") or result == "uncertain":
                        self.reels_db.mark_error(rec.shortcode, result)
                        self._stats["upload_failures_today"] += 1
                        log.warning("🔄 Retry failed for %s: %s", rec.shortcode, result)
                    else:
                        self.reels_db.mark_uploaded(rec.shortcode, upload_caption=caption)
                        self._stats["uploads_today"] += 1
                        log.info("✅ Retry succeeded for reel %s", rec.shortcode)
                        # Clear retry counter on success
                        self._retry_counts.pop(rec.shortcode, None)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.exception("Retry loop error: %s", exc)
                await asyncio.sleep(120)

    # ------------------------------------------------------------------
    # Catch-up loop: upload downloaded-but-not-uploaded reels
    # ------------------------------------------------------------------

    async def _catchup_upload_loop(self) -> None:
        """Background task: periodically find and upload reels that were downloaded
        but never uploaded (e.g. missed by auto-upload due to timing or errors)."""
        if not self.config.upload.catchup_enabled:
            log.info("Catch-up upload loop is disabled")
            return

        interval = self.config.upload.catchup_interval_minutes * 60
        log.info("Catch-up upload loop enabled: checking every %d min", self.config.upload.catchup_interval_minutes)

        # Initial delay so the system has time to start up
        await asyncio.sleep(120)

        while True:
            try:
                self._reset_daily_stats_if_needed()

                if not self.uploader.is_enabled:
                    await asyncio.sleep(interval)
                    continue

                pending = self.reels_db.get_downloaded_not_uploaded()
                # Only catch up reels with no error (errored ones are handled by retry loop)
                catchable = [r for r in pending if not r.error and r.local_path and Path(r.local_path).exists()]

                if catchable:
                    log.info("📥 Catch-up: found %d downloaded-but-not-uploaded reels", len(catchable))
                    for rec in catchable:
                        if not self.uploader.is_enabled:
                            break
                        await self._wait_upload_gap()

                        caption = rec.caption or ""
                        if self.ai.settings.enabled:
                            try:
                                rephrased = await self.ai.generate_caption(caption)
                                if rephrased and rephrased != caption:
                                    caption = rephrased
                            except Exception:
                                pass

                        log.info("📥 Catch-up upload: reel %s", rec.shortcode)
                        result = await self.uploader.upload(rec.local_path, caption)
                        self._last_upload_time = time.time()

                        if result.startswith("error") or result == "uncertain":
                            self.reels_db.mark_error(rec.shortcode, result)
                            self._stats["upload_failures_today"] += 1
                            log.warning("📥 Catch-up upload failed for %s: %s", rec.shortcode, result)
                        else:
                            self.reels_db.mark_uploaded(rec.shortcode, upload_caption=caption)
                            self._stats["uploads_today"] += 1
                            log.info("✅ Catch-up upload: reel %s uploaded", rec.shortcode)

                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.exception("Catch-up loop error: %s", exc)
                await asyncio.sleep(120)

    # ------------------------------------------------------------------
    # Storage cleanup loop
    # ------------------------------------------------------------------

    async def _storage_cleanup_loop(self) -> None:
        """Background task: delete local files for reels that were uploaded
        more than cleanup_after_days ago."""
        cleanup_days = self.config.upload.cleanup_after_days
        if cleanup_days <= 0 and not self.config.upload.cleanup_after_upload:
            log.info("Storage cleanup is disabled")
            return

        log.info("Storage cleanup enabled: delete uploaded files after %d days", cleanup_days)

        # Run once per hour
        while True:
            try:
                await asyncio.sleep(3600)

                if cleanup_days <= 0:
                    continue

                cutoff = time.time() - (cleanup_days * 86400)
                all_reels = self.reels_db.get_all()
                cleaned = 0
                for rec in all_reels:
                    if (rec.uploaded and rec.uploaded_at > 0
                            and rec.uploaded_at < cutoff
                            and rec.local_path):
                        fp = Path(rec.local_path)
                        if fp.exists():
                            fp.unlink()
                            cleaned += 1
                            log.debug("🗑 Cleaned up %s (uploaded %d days ago)",
                                      fp.name, int((time.time() - rec.uploaded_at) / 86400))

                if cleaned:
                    log.info("🗑 Storage cleanup: deleted %d old uploaded files", cleaned)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.exception("Cleanup loop error: %s", exc)
                await asyncio.sleep(300)

    # ------------------------------------------------------------------
    # Scheduled upload loop
    # ------------------------------------------------------------------

    async def _upload_scheduler(self) -> None:
        """Background task: upload reels_per_schedule reels at each scheduled time."""
        if not self.config.upload.schedule_enabled:
            return

        rps = self.config.upload.reels_per_schedule
        log.info(
            "Upload scheduler started – times: %s, %d reels per slot, %ds gap",
            self.config.upload.schedule_times, rps,
            self.config.upload.delay_between_uploads,
        )

        triggered_today: set = set()  # Track which schedule times already triggered today
        last_date = ""

        while True:
            try:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                current_time = now.strftime("%H:%M")

                # Reset triggered set at midnight
                if today != last_date:
                    triggered_today.clear()
                    last_date = today

                if (current_time in self.config.upload.schedule_times
                        and current_time not in triggered_today):
                    triggered_today.add(current_time)

                    # Count pending reels
                    pending = self.reels_db.get_downloaded_not_uploaded()
                    if not pending:
                        log.info("⏰ Schedule %s: no reels to upload", current_time)
                    else:
                        to_upload = min(rps, len(pending))
                        log.info(
                            "⏰ Schedule %s triggered – uploading %d/%d pending reels",
                            current_time, to_upload, len(pending),
                        )
                        count = await self.upload_all_pending(max_count=rps)
                        log.info(
                            "⏰ Schedule %s done – %d reels uploaded",
                            current_time, count,
                        )

                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.exception("Upload scheduler error: %s", exc)
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Startup / Shutdown
    # ------------------------------------------------------------------

    async def run(self) -> None:
        mode = "download + upload" if self.config.upload.enabled else "download only"
        log.info("=" * 60)
        log.info("  Instagram Reel Monitor – %s", mode)
        log.info("  Targets: %s", ", ".join(
            f"@{t.username} (>={t.min_views} views)" for t in self.config.targets
        ))
        log.info("  Poll interval: %ds", self.config.poll_interval_seconds)
        log.info("  Storage: %s", self.config.storage.local_dir)
        if self.config.upload.enabled:
            log.info("  Upload: @%s | auto=%s | schedule=%s | retry=%s | catchup=%s",
                     self.config.my_account.username,
                     self.config.upload.auto_upload,
                     self.config.upload.schedule_enabled,
                     self.config.upload.retry_failed,
                     self.config.upload.catchup_enabled)
        if self.config.ai.enabled:
            log.info("  AI helper: enabled (%s)", self.config.ai.model)
        log.info("=" * 60)

        await self.queue.start(self._process_job)
        await self.monitor.start()

        background_tasks: list[asyncio.Task] = []

        # Start upload scheduler
        if self.config.upload.schedule_enabled and self.uploader.is_enabled:
            background_tasks.append(asyncio.create_task(
                self._upload_scheduler(), name="upload-scheduler"
            ))

        # Start failed upload retry loop
        if self.config.upload.retry_failed and self.uploader.is_enabled:
            background_tasks.append(asyncio.create_task(
                self._retry_failed_uploads(), name="retry-loop"
            ))

        # Start catch-up upload loop
        if self.config.upload.catchup_enabled and self.uploader.is_enabled:
            background_tasks.append(asyncio.create_task(
                self._catchup_upload_loop(), name="catchup-loop"
            ))

        # Start storage cleanup loop
        if self.config.upload.cleanup_after_days > 0 or self.config.upload.cleanup_after_upload:
            background_tasks.append(asyncio.create_task(
                self._storage_cleanup_loop(), name="cleanup-loop"
            ))

        # Start web dashboard
        web_task: Optional[asyncio.Task] = None
        if self.config.web.enabled:
            from .web_interface import app as fastapi_app, configure
            configure(self.monitor, self.queue, self.state, self)

            # Kill any old process holding the port
            await self._free_port(self.config.web.port)

            web_config = uvicorn.Config(
                app=fastapi_app,
                host=self.config.web.host,
                port=self.config.web.port,
                log_level="warning",
            )
            server = uvicorn.Server(web_config)
            web_task = asyncio.create_task(
                self._safe_web_server(server), name="web-server"
            )
            log.info("Dashboard at http://%s:%d", self.config.web.host, self.config.web.port)

        log.info("All background tasks started (%d). Running 24/7 …", len(background_tasks))

        # Block until Ctrl-C – Windows-compatible signal handling
        stop_event = asyncio.Event()

        def _signal_handler():
            log.info("Shutdown signal received")
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows: signal handlers not supported in asyncio
                pass

        # On Windows, also handle Ctrl-C via a background thread
        import sys
        if sys.platform == "win32":
            import threading

            def _win_signal_waiter():
                try:
                    signal.signal(signal.SIGINT, lambda s, f: None)
                    while not stop_event.is_set():
                        import time as _t
                        _t.sleep(0.5)
                except (KeyboardInterrupt, SystemExit):
                    loop.call_soon_threadsafe(stop_event.set)

            threading.Thread(target=_win_signal_waiter, daemon=True).start()

        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass

        log.info("Shutting down …")
        await self.monitor.stop()
        await self.queue.stop()
        await self.downloader.close()

        for task in background_tasks:
            task.cancel()
        if web_task:
            web_task.cancel()

        all_cancel = background_tasks + ([web_task] if web_task else [])
        await asyncio.gather(*all_cancel, return_exceptions=True)

        log.info("Goodbye.")


    @staticmethod
    async def _safe_web_server(server: uvicorn.Server) -> None:
        """Run uvicorn and absorb errors so the app keeps running."""
        try:
            await server.serve()
        except OSError as exc:
            log.warning("Web dashboard failed to start: %s – app continues without dashboard", exc)
        except Exception as exc:
            log.warning("Web dashboard crashed: %s – app continues", exc)

    @staticmethod
    async def _free_port(port: int) -> None:
        """Kill any process currently holding the given port (Windows)."""
        import subprocess
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != 0:
                        log.info("Killing old process on port %d (PID %s)", port, pid)
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=5,
                        )
                        await asyncio.sleep(1)  # Give OS time to release
                        break
        except Exception as exc:
            log.debug("Could not free port %d: %s", port, exc)


def main(config_path: Optional[str] = None, visible: bool = False) -> None:
    config = load_config(config_path)
    setup_logging(level=config.log_level, log_file=config.log_file)

    # If --visible passed via CLI, force headed mode
    if visible:
        config.upload.headless = False
    else:
        # Interactive prompt: ask user every time
        print()
        print("\033[1;36m" + "=" * 50 + "\033[0m")
        print("\033[1;36m  Instagram Reel Monitor\033[0m")
        print("\033[1;36m" + "=" * 50 + "\033[0m")
        print()
        try:
            choice = input("\033[1;33mShow browser while working? (y=headed / n=headless) [n]: \033[0m").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"

        if choice in ("y", "yes"):
            config.upload.headless = False
            print("\033[32m→ Browser will be VISIBLE (headed mode).\033[0m")
        else:
            config.upload.headless = True
            print("\033[32m→ Browser will run in the BACKGROUND (headless mode).\033[0m")
        print()

    log.info("Starting Instagram Reel Monitor (headless=%s)", config.upload.headless)

    application = Application(config)
    try:
        asyncio.run(application.run())
    except KeyboardInterrupt:
        pass
