"""
Persistent state management.

Tracks the last-seen reel ID for each monitored account so the system
can resume after crashes or restarts without reprocessing old reels.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .logger import get_logger

log = get_logger("state")


class StateManager:
    """Thread-safe JSON-backed state store.

    Structure on disk::

        {
            "accounts": {
                "<user_id>": {
                    "last_reel_id": "...",
                    "last_reel_timestamp": "...",
                    "processed_reel_ids": ["..."]
                }
            }
        }
    """

    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                log.info("State loaded from %s", self._path)
                return data
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Failed to load state file, starting fresh: %s", exc)
        return {"accounts": {}}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2)
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_last_reel_id(self, user_id: str) -> Optional[str]:
        with self._lock:
            acct = self._state.get("accounts", {}).get(user_id, {})
            return acct.get("last_reel_id")

    def set_last_reel_id(
        self, user_id: str, reel_id: str, timestamp: str = ""
    ) -> None:
        with self._lock:
            accounts = self._state.setdefault("accounts", {})
            acct = accounts.setdefault(user_id, {})
            acct["last_reel_id"] = reel_id
            if timestamp:
                acct["last_reel_timestamp"] = timestamp
            processed: list = acct.setdefault("processed_reel_ids", [])
            if reel_id not in processed:
                processed.append(reel_id)
                # Keep only last 500 to avoid unbounded growth
                if len(processed) > 500:
                    acct["processed_reel_ids"] = processed[-500:]
            self._save()
            log.debug("State updated for %s – last_reel_id=%s", user_id, reel_id)

    def is_reel_processed(self, user_id: str, reel_id: str) -> bool:
        with self._lock:
            acct = self._state.get("accounts", {}).get(user_id, {})
            return reel_id in acct.get("processed_reel_ids", [])

    # ------------------------------------------------------------------
    #  Rejected-reel tracking (below min_views)
    # ------------------------------------------------------------------

    def mark_reel_rejected(self, user_id: str, reel_id: str, view_count: int = 0) -> None:
        """Record that a reel was downloaded but rejected (below min_views).

        Stores the rejection timestamp so the reel can be re-checked later.
        """
        import time as _time
        with self._lock:
            accounts = self._state.setdefault("accounts", {})
            acct = accounts.setdefault(user_id, {})
            rejected: dict = acct.setdefault("rejected_reels", {})
            rejected[reel_id] = {
                "rejected_at": _time.time(),
                "view_count": view_count,
            }
            # Cap rejected list to 1000 entries — remove oldest
            if len(rejected) > 1000:
                sorted_ids = sorted(rejected, key=lambda k: rejected[k].get("rejected_at", 0))
                for old_id in sorted_ids[:len(rejected) - 1000]:
                    del rejected[old_id]
            self._save()
            log.debug(
                "Reel %s rejected for %s (views=%d)", reel_id, user_id, view_count
            )

    def is_reel_rejected(self, user_id: str, reel_id: str, recheck_days: int = 10) -> bool:
        """Check if a reel was rejected recently (within recheck_days).

        Returns True if the reel should be SKIPPED (still in cooldown).
        Returns False if the reel was never rejected or the cooldown expired.
        """
        import time as _time
        with self._lock:
            acct = self._state.get("accounts", {}).get(user_id, {})
            rejected = acct.get("rejected_reels", {})
            if reel_id not in rejected:
                return False
            rejected_at = rejected[reel_id].get("rejected_at", 0)
            age_days = (_time.time() - rejected_at) / 86400
            if age_days >= recheck_days:
                # Cooldown expired — allow re-check
                log.info(
                    "Reel %s was rejected %.1f days ago — allowing re-check",
                    reel_id, age_days,
                )
                return False
            return True

    def clear_rejected_reel(self, user_id: str, reel_id: str) -> None:
        """Remove a reel from the rejected list (e.g. if it now qualifies)."""
        with self._lock:
            acct = self._state.get("accounts", {}).get(user_id, {})
            rejected = acct.get("rejected_reels", {})
            if reel_id in rejected:
                del rejected[reel_id]
                self._save()

    def get_last_poll_time(self, user_id: str) -> float:
        """Return Unix timestamp of the last successful poll for this user."""
        with self._lock:
            acct = self._state.get("accounts", {}).get(user_id, {})
            return float(acct.get("last_poll_time", 0))

    def set_last_poll_time(self, user_id: str, ts: float | None = None) -> None:
        """Persist the last poll timestamp."""
        import time as _time
        with self._lock:
            accounts = self._state.setdefault("accounts", {})
            acct = accounts.setdefault(user_id, {})
            acct["last_poll_time"] = ts if ts is not None else _time.time()
            self._save()

    def get_all_state(self) -> Dict[str, Any]:
        """Return a copy of the full state (for the web dashboard)."""
        with self._lock:
            return json.loads(json.dumps(self._state))
