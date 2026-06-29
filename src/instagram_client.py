"""
Instagram scraper using SeleniumBase with AI-guided navigation.

At every key step the scraper takes a screenshot and asks the NVIDIA AI
what is on screen and what to do next.  AI responses are cached so the
same question is never asked twice.  Screenshots are saved under
./screenshots/ for the user to inspect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import AISettings, RetrySettings, TargetAccount
from .logger import get_logger

log = get_logger("instagram_client")

_SCREENSHOTS_DIR = Path("./screenshots")
_COOKIES_DIR = Path("./cookies")

# Module-level AI cache – survives across scraper calls
_ai_cache: Dict[str, str] = {}


@dataclass
class ReelInfo:
    """Represents a single Instagram Reel."""

    shortcode: str
    media_id: str = ""
    video_url: str = ""
    thumbnail_url: str = ""
    caption: str = ""
    timestamp: str = ""
    view_count: int = 0
    like_count: int = 0
    permalink: str = ""
    username: str = ""
    hashtags: List[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.shortcode


class InstagramScraper:
    """Scrapes Instagram reels using SeleniumBase + AI guidance."""

    def __init__(
        self,
        retry: RetrySettings | None = None,
        login_user: str = "",
        login_pass: str = "",
        headless: bool = True,
        ai_settings: AISettings | None = None,
    ) -> None:
        self.retry = retry or RetrySettings()
        self._login_user = login_user
        self._login_pass = login_pass
        self.headless = headless
        self._ai = ai_settings
        _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        _COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        self._cookies_file = _COOKIES_DIR / f"{login_user or 'anon'}_cookies.json"
        self._consecutive_cookie_failures = 0  # Track cookie failures to force credential login

    # ==================================================================
    #  AI helpers  (sync – runs inside a thread-pool worker)
    # ==================================================================

    def _take_screenshot(self, sb: Any, label: str) -> str:
        """Save a screenshot and return its path."""
        safe = re.sub(r'[^\w\-]', '_', label)[:40]
        ts = int(time.time())
        path = _SCREENSHOTS_DIR / f"{safe}_{ts}.png"
        try:
            sb.save_screenshot(str(path))
            log.info("📸 Screenshot saved → %s", path.name)
        except Exception as exc:
            log.debug("Screenshot failed: %s", exc)
            return ""
        return str(path)

    def _extract_page_text(self, sb: Any) -> str:
        """Get visible text from the page (truncated)."""
        try:
            body = sb.find_element("body")
            return (body.text or "")[:3000]
        except Exception:
            return ""

    def _ask_ai_sync(self, prompt: str, max_tokens: int = 512) -> str:
        """Synchronous call to the NVIDIA AI (runs in worker thread)."""
        if not self._ai or not self._ai.enabled or not self._ai.api_key:
            return ""
        import requests as req

        headers = {
            "Authorization": f"Bearer {self._ai.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._ai.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.10,
            "top_p": 1.0,
            "stream": False,
        }
        try:
            resp = req.post(
                self._ai.invoke_url, headers=headers, json=payload, timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
            else:
                log.warning("AI API returned %d", resp.status_code)
        except Exception as exc:
            log.warning("AI sync call failed: %s", exc)
        return ""

    def _analyze_page(self, sb: Any, context: str) -> str:
        """Screenshot → extract text → ask AI → cache the response."""
        self._take_screenshot(sb, context)

        page_text = self._extract_page_text(sb)
        url = sb.get_current_url()
        title = ""
        try:
            title = sb.get_title() or ""
        except Exception:
            pass

        # Cache key from context + url pattern + first chunk of text
        raw_key = f"{context}|{url}|{title}|{page_text[:300]}"
        cache_hash = hashlib.md5(raw_key.encode()).hexdigest()

        if cache_hash in _ai_cache:
            log.info("🧠 AI cache hit for: %s", context)
            return _ai_cache[cache_hash]

        prompt = (
            "You are helping automate Instagram browsing with Selenium.\n"
            f"CONTEXT: {context}\n"
            f"CURRENT URL: {url}\n"
            f"PAGE TITLE: {title}\n"
            f"VISIBLE TEXT (truncated):\n{page_text[:2000]}\n\n"
            "Based on the above, answer EXACTLY in this format:\n"
            "STATE: <one of: login_wall, cookie_consent, profile_page, reels_tab, "
            "reel_page, age_gate, suspicious_activity, two_factor, not_found, error, other>\n"
            "PROBLEM: <one-line description of what's blocking, or 'none'>\n"
            "ACTION: <specific next step to fix it, e.g. 'click Accept cookies button', "
            "'type credentials and log in', 'scroll down to load reels', 'none'>\n"
        )

        guidance = self._ask_ai_sync(prompt)
        if guidance:
            _ai_cache[cache_hash] = guidance
            log.info("🧠 AI says [%s]: %s", context,
                     guidance.replace('\n', ' | ')[:200])
        return guidance

    def _parse_ai_state(self, guidance: str) -> str:
        """Extract the STATE value from AI guidance."""
        m = re.search(r'STATE:\s*(\S+)', guidance, re.IGNORECASE)
        return m.group(1).lower().strip().rstrip(',') if m else "unknown"

    def _act_on_guidance(self, sb: Any, guidance: str) -> bool:
        """Try to act on AI guidance. Returns True if an action was taken."""
        state = self._parse_ai_state(guidance)

        if state == "cookie_consent":
            log.info("AI detected cookie consent → dismissing …")
            self._dismiss_popups(sb)
            sb.sleep(1)
            return True

        if state == "login_wall":
            if self._login_user and self._login_pass:
                log.info("AI detected login wall → logging in …")
                try:
                    self._do_login(sb)
                    return True
                except Exception as exc:
                    log.warning("Login after AI guidance failed: %s", exc)
            else:
                log.warning(
                    "⚠ AI detected login wall but NO credentials in config.json! "
                    "Set my_account.username and my_account.password."
                )
            return False

        if state == "age_gate":
            log.info("AI detected age gate → confirming …")
            for sel in ['button:contains("Confirm")', 'button:contains("Yes")',
                        'button:contains("Continue")', 'button[type="submit"]']:
                try:
                    if sb.is_element_visible(sel):
                        sb.click(sel)
                        sb.sleep(1)
                        return True
                except Exception:
                    pass
            return False

        if state == "suspicious_activity":
            log.warning("AI detected suspicious-activity challenge – waiting 30s …")
            sb.sleep(30)
            return False

        if state == "two_factor":
            log.warning("AI detected 2FA prompt – cannot proceed automatically")
            return False

        if state in ("profile_page", "reels_tab", "reel_page"):
            return True  # page is fine

        # Default: try dismissing popups
        self._dismiss_popups(sb)
        return False

    # ==================================================================
    #  Core scraping
    # ==================================================================

    def _backoff_delay(self, attempt: int) -> float:
        delay = min(
            self.retry.base_delay * (self.retry.exponential_base ** (attempt - 1)),
            self.retry.max_delay,
        )
        jitter = delay * 0.25 * (2 * random.random() - 1)
        return max(1.0, delay + jitter)

    async def get_reels(
        self,
        target: TargetAccount,
        max_count: int = 20,
    ) -> List[ReelInfo]:
        """Fetch recent reels from a public profile using SeleniumBase.

        Runs in thread pool to keep the event loop free.
        """
        loop = asyncio.get_running_loop()
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                reels = await loop.run_in_executor(
                    None, self._scrape_profile, target, max_count
                )
                return reels
            except Exception as exc:
                delay = self._backoff_delay(attempt)
                log.warning(
                    "Scrape error for @%s (attempt %d/%d): %s – retrying in %.1fs",
                    target.username, attempt, self.retry.max_attempts, exc, delay,
                )
                await asyncio.sleep(delay)

        log.error("All %d scrape retries exhausted for @%s", self.retry.max_attempts, target.username)
        return []

    def _scrape_profile(self, target: TargetAccount, max_count: int) -> List[ReelInfo]:
        """Open browser → login (cookies or fresh) → navigate to reels → scrape."""
        from seleniumbase import SB

        reels: List[ReelInfo] = []
        reels_url = f"https://www.instagram.com/{target.username}/reels/"

        with SB(uc=True, headless=self.headless, locale_code="en") as sb:

            # ════════════════════════════════════════════════════════
            #  PHASE 1: Establish logged-in session
            # ════════════════════════════════════════════════════════
            logged_in = False

            # 1a. Try saved cookies first (fastest, avoids password)
            if self._login_user and self._cookies_file.exists():
                logged_in = self._load_cookies(sb)
                if logged_in:
                    log.info("🍪 Session restored from cookies")
                    self._consecutive_cookie_failures = 0  # Reset counter on success

            # 1b. Fresh login with credentials (only when cookies don't exist or are expired)
            if not logged_in and self._login_user and self._login_pass:
                # Check if cookies file exists – if it does, the failure was likely
                # a transient network error, not expired cookies. Skip credential login
                # and let the retry loop handle it (up to 2 times).
                if self._cookies_file.exists():
                    self._consecutive_cookie_failures += 1
                    if self._consecutive_cookie_failures <= 2:
                        log.info("🍪 Cookies exist but couldn't verify – will retry (attempt %d/2)", 
                                 self._consecutive_cookie_failures)
                        raise RuntimeError("Transient cookie restore failure – retry")
                    else:
                        log.warning("🍪 Cookie verification failed %d times – deleting cookies and doing fresh login",
                                    self._consecutive_cookie_failures)
                        self._cookies_file.unlink(missing_ok=True)
                        self._consecutive_cookie_failures = 0
                log.info("No cookies found – logging in with credentials …")
                try:
                    self._do_login(sb)
                    logged_in = True
                except Exception as exc:
                    log.warning("Login failed: %s", exc)
                    guidance = self._analyze_page(sb, "login_failed")
                    self._act_on_guidance(sb, guidance)

            # 1c. Verify we're actually logged in
            if logged_in:
                current = sb.get_current_url()
                page_text = self._extract_page_text(sb)
                if "login" in current.lower() and "log in" in page_text.lower():
                    log.warning("Session lost – attempting fresh login …")
                    self._cookies_file.unlink(missing_ok=True)
                    try:
                        self._do_login(sb)
                        logged_in = True
                    except Exception as exc:
                        log.error("Fresh login also failed: %s", exc)
                        logged_in = False

            if not logged_in:
                log.warning("⚠ Not logged in – scraping may be limited")

            # ════════════════════════════════════════════════════════
            #  PHASE 2: Human warmup
            # ════════════════════════════════════════════════════════
            self._human_warmup(sb)

            # ════════════════════════════════════════════════════════
            #  PHASE 3: Navigate to the reels page
            # ════════════════════════════════════════════════════════
            log.info("Opening %s …", reels_url)
            sb.open(reels_url)
            sb.sleep(4)
            self._dismiss_popups(sb)

            # AI check: what's on screen?
            guidance = self._analyze_page(sb, "after_opening_reels_tab")
            state = self._parse_ai_state(guidance)

            # Handle blocks
            if state == "login_wall":
                log.info("Login wall on reels page – logging in …")
                acted = self._act_on_guidance(sb, guidance)
                if acted:
                    sb.sleep(2)
                    sb.open(reels_url)
                    sb.sleep(4)
                    self._dismiss_popups(sb)
            elif state in ("cookie_consent", "age_gate"):
                self._act_on_guidance(sb, guidance)
                sb.sleep(2)
                self._dismiss_popups(sb)
            elif state == "not_found":
                log.error("Profile @%s not found!", target.username)
                self._take_screenshot(sb, "profile_not_found")
                return []

            # Double check profile exists
            page_src = sb.get_page_source()
            if "Sorry, this page" in page_src or "Page Not Found" in page_src:
                log.error("Profile @%s not found!", target.username)
                self._take_screenshot(sb, "profile_not_found")
                return []

            # Confirm we're on the right page
            current_url = sb.get_current_url()
            if target.username.lower() not in current_url.lower():
                log.warning("Redirected to %s – re-navigating to %s", current_url, reels_url)
                sb.open(reels_url)
                sb.sleep(4)
                self._dismiss_popups(sb)

            log.info("✅ On reels page for @%s", target.username)
            self._take_screenshot(sb, "reels_page_loaded")

            # ════════════════════════════════════════════════════════
            #  PHASE 4: Scroll and collect reel links + view counts
            #  Scroll aggressively to load ALL reels from the profile.
            # ════════════════════════════════════════════════════════
            reel_data: dict = {}  # href -> view_count
            scroll_attempts = 0
            # Scale scroll limit based on how many reels we want
            max_scrolls = max(50, max_count // 3)
            stale_rounds = 0  # Track rounds where no new reels are found
            max_stale = 6     # Allow more stale rounds — IG loads in batches
            last_height = 0

            log.info(
                "Scrolling to load reels (target: %d, max scrolls: %d) …",
                max_count, max_scrolls,
            )

            while scroll_attempts < max_scrolls and stale_rounds < max_stale:
                prev_count = len(reel_data)

                # Use JS to extract reel links AND their associated view counts
                entries = sb.execute_script("""
                    var results = [];
                    var links = document.querySelectorAll('a[href*="/reel/"]');
                    for (var i = 0; i < links.length; i++) {
                        var href = links[i].href;
                        if (!href || href.indexOf('/reel/') === -1) continue;
                        var viewText = '';
                        var spans = links[i].querySelectorAll('span');
                        for (var j = 0; j < spans.length; j++) {
                            var t = spans[j].textContent.trim();
                            if (/^[\d,.]+[KMBkmb]?$/.test(t) && t.length > 0 && t.length < 15) {
                                viewText = t;
                                break;
                            }
                        }
                        if (!viewText) {
                            var elems = links[i].querySelectorAll('li, div > span');
                            for (var k = 0; k < elems.length; k++) {
                                var txt = elems[k].textContent.trim();
                                if (/^[\d,.]+[KMBkmb]?$/.test(txt) && txt.length > 0 && txt.length < 15) {
                                    viewText = txt;
                                    break;
                                }
                            }
                        }
                        results.push({href: href, views: viewText});
                    }
                    return results;
                """)

                if entries:
                    for entry in entries:
                        href = entry.get("href", "")
                        views_text = entry.get("views", "")
                        if href and "/reel/" in href and href not in reel_data:
                            reel_data[href] = views_text

                new_found = len(reel_data) - prev_count

                # Check if page height changed (content still loading)
                current_height = sb.execute_script("return document.body.scrollHeight;") or 0
                height_changed = current_height != last_height
                last_height = current_height

                if new_found == 0 and not height_changed:
                    stale_rounds += 1
                else:
                    stale_rounds = 0

                # Log progress every 10 scrolls
                if scroll_attempts > 0 and scroll_attempts % 10 == 0:
                    log.info(
                        "  … scroll %d: %d reels found so far (stale: %d/%d)",
                        scroll_attempts, len(reel_data), stale_rounds, max_stale,
                    )

                # Scroll to bottom of page for maximum speed
                sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                # Wait for lazy-load; vary the wait slightly
                sb.sleep(random.uniform(1.5, 3.0))
                scroll_attempts += 1

            log.info(
                "Found %d reel links on @%s's page after %d scrolls",
                len(reel_data), target.username, scroll_attempts,
            )

            # If still 0, ask AI
            if len(reel_data) == 0:
                guidance = self._analyze_page(sb, "zero_reels_found")
                ai_state = self._parse_ai_state(guidance)
                acted = self._act_on_guidance(sb, guidance)

                if acted and ai_state == "login_wall":
                    sb.open(reels_url)
                    sb.sleep(4)
                    self._dismiss_popups(sb)
                    for _ in range(5):
                        links = sb.find_elements("a[href*='/reel/']")
                        for link in links:
                            try:
                                href = link.get_attribute("href")
                                if href and "/reel/" in href:
                                    reel_data[href] = ""
                            except Exception:
                                pass
                        if reel_data:
                            break
                        sb.execute_script("window.scrollBy(0, 1000);")
                        sb.sleep(2)
                    log.info("After AI fix: found %d reel links", len(reel_data))

                if len(reel_data) == 0:
                    self._take_screenshot(sb, "still_zero_reels")
                    log.warning("Still 0 reels – will retry next poll")
                    return []

            # ════════════════════════════════════════════════════════
            #  PHASE 5: Extract shortcodes and build ReelInfo list.
            #  Grid view counts are unreliable (often wrong), so we
            #  include all reels here. Actual view count verification
            #  happens via yt-dlp metadata before downloading.
            # ════════════════════════════════════════════════════════
            for href, views_text in list(reel_data.items()):
                match = re.search(r"/reel/([A-Za-z0-9_-]+)", href)
                if not match:
                    continue
                shortcode = match.group(1)
                permalink = f"https://www.instagram.com/reel/{shortcode}/"

                # Parse grid view count as a hint (may be inaccurate)
                grid_views = self._parse_count(views_text) if views_text else 0

                reel = ReelInfo(
                    shortcode=shortcode,
                    permalink=permalink,
                    username=target.username,
                    view_count=grid_views,
                )
                reels.append(reel)
                log.info(
                    "  📌 Discovered reel %s → %s (grid hint: %s)",
                    shortcode, permalink, views_text or "n/a",
                )

                if len(reels) >= max_count:
                    break

            # ════════════════════════════════════════════════════════
            #  PHASE 6: Cooldown
            # ════════════════════════════════════════════════════════
            self._human_cooldown(sb)

        log.info(
            "Discovered %d reels from @%s",
            len(reels), target.username,
        )
        return reels

    def _scrape_single_reel(self, sb: Any, href: str, target: TargetAccount) -> Optional[ReelInfo]:
        """Navigate to a reel page and extract metadata."""
        match = re.search(r"/reel/([A-Za-z0-9_-]+)", href)
        if not match:
            return None
        shortcode = match.group(1)

        sb.open(href)
        sb.sleep(2)
        self._dismiss_popups(sb)

        view_count = 0
        like_count = 0
        caption = ""
        timestamp = ""

        page_source = sb.get_page_source()

        # -- Extract view count --
        view_patterns = [
            r'([\d,]+)\s*views',
            r'([\d.]+[KMB])\s*views',
            r'"video_view_count"\s*:\s*(\d+)',
            r'"view_count"\s*:\s*(\d+)',
            r'"play_count"\s*:\s*(\d+)',
        ]
        for pattern in view_patterns:
            m = re.search(pattern, page_source, re.IGNORECASE)
            if m:
                view_count = self._parse_count(m.group(1))
                break

        # -- Extract like count --
        like_patterns = [
            r'([\d,]+)\s*likes?',
            r'([\d.]+[KMB])\s*likes?',
            r'"like_count"\s*:\s*(\d+)',
            r'"edge_media_preview_like"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        ]
        for pattern in like_patterns:
            m = re.search(pattern, page_source, re.IGNORECASE)
            if m:
                like_count = self._parse_count(m.group(1))
                break

        # If 0 views, page might be blocked – ask AI once
        if view_count == 0:
            visible = self._extract_page_text(sb)
            if len(visible) < 200 or "log in" in visible.lower():
                guidance = self._analyze_page(sb, f"reel_{shortcode}_zero_views")
                reel_state = self._parse_ai_state(guidance)
                if reel_state == "login_wall":
                    log.warning("Login wall on reel page – counts will be 0")

        # -- Extract caption from meta tags --
        try:
            meta = sb.find_element('meta[property="og:description"]')
            if meta:
                caption = meta.get_attribute("content") or ""
        except Exception:
            pass

        if not caption:
            try:
                meta = sb.find_element('meta[name="description"]')
                if meta:
                    caption = meta.get_attribute("content") or ""
            except Exception:
                pass

        # -- Extract hashtags --
        hashtags = re.findall(r'#\w+', caption)

        # -- Extract timestamp --
        ts_match = re.search(r'"taken_at"\s*:\s*(\d+)', page_source)
        if ts_match:
            import datetime
            timestamp = datetime.datetime.fromtimestamp(
                int(ts_match.group(1))
            ).isoformat()

        permalink = f"https://www.instagram.com/reel/{shortcode}/"

        return ReelInfo(
            shortcode=shortcode,
            caption=caption,
            timestamp=timestamp,
            view_count=view_count,
            like_count=like_count,
            permalink=permalink,
            username=target.username,
            hashtags=hashtags,
        )

    # ==================================================================
    #  Cookie / session management
    # ==================================================================

    def _save_cookies(self, sb: Any) -> None:
        """Save browser cookies to disk for reuse next session."""
        try:
            cookies = sb.get_cookies()
            with open(self._cookies_file, "w", encoding="utf-8") as fh:
                json.dump(cookies, fh)
            log.info("🍪 Saved %d cookies to %s", len(cookies), self._cookies_file.name)
        except Exception as exc:
            log.debug("Could not save cookies: %s", exc)

    def _load_cookies(self, sb: Any) -> bool:
        """Load cookies from disk and check if session is still valid.
        
        Retries the initial page load in case of transient WebSocket/network errors.
        Only falls back to credential login when cookies are genuinely expired.
        """
        if not self._cookies_file.exists():
            return False
        try:
            with open(self._cookies_file, "r", encoding="utf-8") as fh:
                cookies = json.load(fh)
            if not cookies:
                return False

            # Navigate to Instagram first (cookies need matching domain)
            # Retry up to 3 times for transient WebSocket/network errors
            page_loaded = False
            for attempt in range(3):
                try:
                    sb.open("https://www.instagram.com/")
                    sb.sleep(2)
                    self._dismiss_popups(sb)
                    page_loaded = True
                    break
                except Exception as nav_exc:
                    log.warning("Cookie restore: page load attempt %d failed: %s", attempt + 1, nav_exc)
                    if attempt < 2:
                        sb.sleep(3)

            if not page_loaded:
                log.warning("Cannot reach Instagram to restore cookies – will retry next cycle")
                return False

            for cookie in cookies:
                # Remove problematic fields that Selenium doesn't accept
                for key in ("sameSite", "httpOnly", "expiry", "storeId"):
                    cookie.pop(key, None)
                try:
                    sb.add_cookie(cookie)
                except Exception:
                    pass

            # Reload page with cookies applied
            sb.open("https://www.instagram.com/")
            sb.sleep(3)
            self._dismiss_popups(sb)

            # Check if we're actually logged in
            url = sb.get_current_url()
            page_text = self._extract_page_text(sb)
            if "login" in url.lower() and "log in" in page_text.lower():
                log.info("🍪 Cookies expired – will log in with credentials")
                self._cookies_file.unlink(missing_ok=True)
                return False

            log.info("🍪 Cookie login successful")
            # Re-save cookies to refresh expiry timestamps
            self._save_cookies(sb)
            return True
        except Exception as exc:
            log.debug("Cookie load failed: %s", exc)
            # Don't delete cookies on transient errors – they may still be valid
            return False

    # ==================================================================
    #  Anti-bot human-like behavior
    # ==================================================================

    def _human_warmup(self, sb: Any) -> None:
        """Do random human-like actions before scraping to look natural."""
        actions = [
            self._human_scroll_around,
            self._human_visit_explore,
            self._human_hover_random,
            self._human_pause,
        ]
        # Pick 2-3 random actions
        chosen = random.sample(actions, k=min(random.randint(2, 3), len(actions)))
        log.info("🤖→🧑 Doing %d warm-up actions to look human …", len(chosen))
        for action in chosen:
            try:
                action(sb)
            except Exception:
                pass

    def _human_cooldown(self, sb: Any) -> None:
        """Random cool-down actions after scraping."""
        actions = [
            self._human_scroll_around,
            self._human_pause,
            self._human_hover_random,
        ]
        chosen = random.sample(actions, k=random.randint(1, 2))
        log.info("🧑 Doing %d cool-down actions …", len(chosen))
        for action in chosen:
            try:
                action(sb)
            except Exception:
                pass

    def _human_scroll_around(self, sb: Any) -> None:
        """Scroll up and down randomly like a person browsing."""
        scrolls = random.randint(2, 5)
        for _ in range(scrolls):
            direction = random.choice([300, 500, -200, -100, 700, 400])
            sb.execute_script(f"window.scrollBy(0, {direction});")
            sb.sleep(random.uniform(0.5, 1.5))

    def _human_visit_explore(self, sb: Any) -> None:
        """Briefly visit the Explore page like a normal user."""
        log.debug("Human action: visiting Explore page")
        sb.open("https://www.instagram.com/explore/")
        sb.sleep(random.uniform(2, 4))
        self._dismiss_popups(sb)
        # Scroll a bit
        sb.execute_script(f"window.scrollBy(0, {random.randint(300, 800)});")
        sb.sleep(random.uniform(1, 2))

    def _human_hover_random(self, sb: Any) -> None:
        """Hover over random elements on the page."""
        try:
            elements = sb.find_elements("a")
            if elements:
                targets = random.sample(elements, k=min(3, len(elements)))
                for el in targets:
                    try:
                        sb.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", el
                        )
                        sb.sleep(random.uniform(0.3, 0.8))
                    except Exception:
                        pass
        except Exception:
            pass

    def _human_pause(self, sb: Any) -> None:
        """Just pause for a random duration like someone reading."""
        pause = random.uniform(2, 5)
        log.debug("Human action: pausing %.1fs", pause)
        sb.sleep(pause)

    # ==================================================================
    #  Login & popup handling
    # ==================================================================

    def _do_login(self, sb: Any) -> None:
        """Log in to Instagram via SeleniumBase with AI verification."""
        log.info("Logging in to Instagram as @%s …", self._login_user)
        sb.open("https://www.instagram.com/accounts/login/")
        sb.sleep(4)

        # Aggressively dismiss cookie consent (Instagram EU overlay)
        self._dismiss_popups(sb)
        sb.sleep(1)
        self._dismiss_popups(sb)

        # AI check: what's on screen?
        guidance = self._analyze_page(sb, "login_page_loaded")
        state = self._parse_ai_state(guidance)
        if state == "cookie_consent":
            self._dismiss_popups(sb)
            sb.sleep(2)
            self._dismiss_popups(sb)

        # Wait for the login form to appear – try multiple selectors
        username_selectors = [
            'input[name="username"]',
            'input[aria-label="Phone number, username, or email"]',
            'input[aria-label*="username"]',
            'input[aria-label*="Username"]',
            'input[type="text"]',
        ]
        password_selectors = [
            'input[name="password"]',
            'input[aria-label="Password"]',
            'input[aria-label*="password"]',
            'input[aria-label*="Password"]',
            'input[type="password"]',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'button:contains("Log in")',
            'button:contains("Log In")',
            'div[role="button"]:contains("Log in")',
        ]

        # Find username field with retries
        username_el = None
        for attempt in range(3):
            for sel in username_selectors:
                try:
                    if sb.is_element_present(sel):
                        username_el = sel
                        break
                except Exception:
                    pass
            if username_el:
                break
            log.info("Login form not found yet, waiting … (attempt %d/3)", attempt + 1)
            self._dismiss_popups(sb)
            sb.sleep(3)

        if not username_el:
            # Last resort: use JS to find any text input
            self._take_screenshot(sb, "login_form_not_found")
            log.warning("Login form not found via selectors – trying JS injection")
            try:
                sb.execute_script("""
                    var inputs = document.querySelectorAll('input[type="text"], input[name="username"]');
                    if (inputs.length > 0) { inputs[0].focus(); inputs[0].value = arguments[0]; 
                      inputs[0].dispatchEvent(new Event('input', {bubbles: true})); }
                """, self._login_user)
                sb.sleep(0.5)
                sb.execute_script("""
                    var inputs = document.querySelectorAll('input[type="password"], input[name="password"]');
                    if (inputs.length > 0) { inputs[0].focus(); inputs[0].value = arguments[0];
                      inputs[0].dispatchEvent(new Event('input', {bubbles: true})); }
                """, self._login_pass)
                sb.sleep(0.5)
                sb.execute_script("""
                    var btns = document.querySelectorAll('button[type="submit"]');
                    if (btns.length > 0) { btns[0].click(); }
                """)
                sb.sleep(5)
                self._dismiss_popups(sb)

                guidance = self._analyze_page(sb, "after_js_login")
                state = self._parse_ai_state(guidance)
                if "login" not in sb.get_current_url().lower() or state in (
                    "profile_page", "reels_tab", "other",
                ):
                    log.info("✅ Logged in via JS fallback as @%s", self._login_user)
                    self._save_cookies(sb)
                    return
                raise RuntimeError("JS login fallback did not succeed")
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Cannot find login form: {exc}") from exc

        # Type credentials with small human-like delays
        log.info("Found login form, entering credentials …")
        sb.slow_click(username_el)
        sb.sleep(random.uniform(0.3, 0.7))
        sb.type(username_el, self._login_user)
        sb.sleep(random.uniform(0.5, 1.0))

        # Find and fill password
        password_el = None
        for sel in password_selectors:
            try:
                if sb.is_element_present(sel):
                    password_el = sel
                    break
            except Exception:
                pass
        if not password_el:
            password_el = 'input[type="password"]'

        sb.slow_click(password_el)
        sb.sleep(random.uniform(0.3, 0.7))
        sb.type(password_el, self._login_pass)
        sb.sleep(random.uniform(0.5, 1.2))

        # Find and click submit
        for sel in submit_selectors:
            try:
                if sb.is_element_visible(sel):
                    sb.click(sel)
                    break
            except Exception:
                pass
        sb.sleep(6)

        # Dismiss "Save login info" / "Turn on notifications"
        self._dismiss_popups(sb)
        sb.sleep(1)
        self._dismiss_popups(sb)

        # AI check: did login succeed?
        guidance = self._analyze_page(sb, "after_login_submit")
        state = self._parse_ai_state(guidance)

        if state == "two_factor":
            log.warning("2FA is required – cannot proceed automatically")
            raise RuntimeError("Two-factor authentication required")
        if state == "suspicious_activity":
            log.warning("Instagram flagged suspicious activity")
            raise RuntimeError("Suspicious activity detected by Instagram")

        if "login" not in sb.get_current_url().lower() or state in (
            "profile_page", "reels_tab", "other",
        ):
            log.info("✅ Logged in successfully as @%s", self._login_user)
            self._save_cookies(sb)
        else:
            self._take_screenshot(sb, "login_failed_final")
            raise RuntimeError("Login page still showing – credentials may be wrong")

    def _dismiss_popups(self, sb: Any) -> None:
        """Click away cookie banners, login walls, notification prompts."""
        # First try JS-based cookie consent removal (Instagram's EU overlay)
        try:
            sb.execute_script("""
                // Click any visible "Accept" / "Allow" buttons
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    var txt = (b.textContent || '').toLowerCase().trim();
                    if (txt.includes('allow') || txt.includes('accept') || 
                        txt.includes('not now') || txt.includes('decline') ||
                        txt.includes('close') || txt.includes('turn off')) {
                        if (b.offsetParent !== null) { b.click(); }
                    }
                }
                // Remove any overlay/modal blocking the page
                var overlays = document.querySelectorAll('[role="dialog"], [role="presentation"]');
                for (var o of overlays) {
                    var style = window.getComputedStyle(o);
                    if (style.position === 'fixed' || style.position === 'absolute') {
                        // Check if it's a cookie banner (not the main content)
                        if (o.querySelector('button')) { o.remove(); }
                    }
                }
            """)
        except Exception:
            pass

        dismiss_selectors = [
            'button:contains("Accept")',
            'button:contains("Accept All")',
            'button:contains("Allow essential and optional cookies")',
            'button:contains("Allow all cookies")',
            'button:contains("Only allow essential cookies")',
            'button:contains("Allow")',
            'button:contains("Not Now")',
            'button:contains("Not now")',
            'button:contains("Decline")',
            'button:contains("Close")',
            'button:contains("Turn Off")',
            'button:contains("Save Info")',
            'button:contains("Save information")',
        ]
        for sel in dismiss_selectors:
            try:
                if sb.is_element_visible(sel):
                    sb.click(sel)
                    sb.sleep(0.5)
            except Exception:
                pass

    @staticmethod
    def _parse_count(text: str) -> int:
        """Parse '1,234' or '1.2K' or '3.4M' into an integer."""
        text = text.strip().replace(",", "")
        multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
        for suffix, mult in multipliers.items():
            if text.upper().endswith(suffix):
                try:
                    return int(float(text[:-1]) * mult)
                except ValueError:
                    return 0
        try:
            return int(text)
        except ValueError:
            return 0
