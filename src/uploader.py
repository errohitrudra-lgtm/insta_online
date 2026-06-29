"""
Instagram reel uploader using Playwright Async API.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any, Optional

from .config import AppConfig
from .logger import get_logger

log = get_logger("uploader")

_SCREENSHOTS_DIR = Path("./screenshots")


class ReelUploader:
    """Uploads reel videos to Instagram via Playwright async browser."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._username = config.my_account.username
        self._password = config.my_account.password
        self._headless = config.upload.headless
        self._upload_enabled = config.upload.enabled

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False

        _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        if not self._upload_enabled:
            log.info("Upload is DISABLED in config.")
        elif not self._username or not self._password:
            log.warning("Upload enabled but no credentials set")
        else:
            log.info("Uploader ready for @%s (headless=%s)", self._username, self._headless)

    @property
    def is_enabled(self) -> bool:
        return self._upload_enabled and bool(self._username) and bool(self._password)

    async def _human_delay(self, min_s: float = 1.0, max_s: float = 3.0):
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _ensure_browser(self):
        """Launch browser if not running."""
        if self._page is not None:
            return

        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        # Try to restore session from saved cookies
        storage_state = None

        # First try Playwright format cookies
        playwright_cookies = Path(f"./cookies/{self._username}_playwright.json")
        if playwright_cookies.exists():
            try:
                with open(playwright_cookies, "r") as f:
                    json.load(f)  # validate JSON
                storage_state = str(playwright_cookies)
                log.info("Restoring session from Playwright cookies...")
            except:
                pass

        # If no Playwright cookies, try to convert SeleniumBase cookies
        if not storage_state:
            seleniumbase_cookies = Path(f"./cookies/{self._username}_cookies.json")
            if seleniumbase_cookies.exists():
                try:
                    with open(seleniumbase_cookies, "r") as f:
                        sb_cookies = json.load(f)

                    if isinstance(sb_cookies, list) and len(sb_cookies) > 0:
                        # Convert SeleniumBase format to Playwright storage_state format
                        playwright_state = {
                            "cookies": [],
                            "origins": []
                        }

                        for cookie in sb_cookies:
                            pw_cookie = {
                                "name": cookie.get("name", ""),
                                "value": cookie.get("value", ""),
                                "domain": cookie.get("domain", ".instagram.com"),
                                "path": cookie.get("path", "/"),
                                "secure": cookie.get("secure", True),
                                "httpOnly": cookie.get("httpOnly", False),
                                "sameSite": "Lax",
                            }
                            if "expiry" in cookie:
                                pw_cookie["expires"] = cookie["expiry"]
                            playwright_state["cookies"].append(pw_cookie)

                        # Save converted cookies as Playwright format
                        with open(playwright_cookies, "w") as f:
                            json.dump(playwright_state, f)

                        storage_state = str(playwright_cookies)
                        log.info("Converted SeleniumBase cookies to Playwright format")
                except Exception as e:
                    log.debug("Failed to convert SeleniumBase cookies: %s", e)

        self._context = await self._browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
        )
        self._page = await self._context.new_page()
        await self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

    async def _save_session(self):
        if self._context is None:
            return
        try:
            cookies_dir = Path("./cookies")
            cookies_dir.mkdir(parents=True, exist_ok=True)
            await self._context.storage_state(path=str(cookies_dir / f"{self._username}_playwright.json"))
            log.info("Session cookies saved")
        except Exception as e:
            log.debug("Failed to save session: %s", e)

    async def _dismiss_popups(self):
        page = self._page
        for text in ["Not Now", "Not now", "Accept", "Allow", "Decline", "Skip", "Close"]:
            try:
                btn = page.get_by_role("button", name=text)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    await self._human_delay(0.5, 1)
            except:
                pass

    async def _is_logged_in(self) -> bool:
        page = self._page
        try:
            if "/accounts/login" in page.url.lower():
                return False
            for sel in ['svg[aria-label="Home"]', 'svg[aria-label="New post"]', 'a[href="/direct/inbox/"]']:
                if await page.locator(sel).count() > 0:
                    return True
            return False
        except:
            return False

    async def _login(self):
        if self._logged_in:
            return

        await self._ensure_browser()
        page = self._page

        log.info("Checking login status...")
        await page.goto("https://www.instagram.com/", wait_until="load", timeout=45000)
        await self._human_delay(2, 3)

        # Handle cookie consent
        for _ in range(3):
            try:
                for btn_text in [
                    "Allow all cookies",
                    "Allow essential and optional cookies",
                    "Accept All",
                    "Accept all",
                    "Accept",
                    "Decline optional cookies",
                    "Only allow essential cookies"
                ]:
                    try:
                        btn = page.locator(f"button:has-text('{btn_text}')")
                        if await btn.count() > 0:
                            log.info("Found cookie button: %s", btn_text)
                            await btn.first.click(timeout=3000)
                            await self._human_delay(1, 2)
                            break
                    except:
                        pass

                for btn_text in ["Allow all cookies", "Allow essential and optional cookies"]:
                    try:
                        btn = page.get_by_role("button", name=btn_text)
                        if await btn.count() > 0:
                            await btn.first.click(timeout=3000)
                            await self._human_delay(1, 2)
                            break
                    except:
                        pass
            except:
                pass
            await self._human_delay(0.5, 1)

        await self._dismiss_popups()

        if await self._is_logged_in():
            log.info("Logged in via saved cookies as @%s", self._username)
            self._logged_in = True
            await self._save_session()
            return

        log.info("Cookies expired, logging in with credentials as @%s...", self._username)
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="load", timeout=45000)
        await self._human_delay(2, 3)

        # Handle cookie consent again on login page
        for btn_text in ["Allow all cookies", "Allow essential and optional cookies", "Accept"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')")
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    await self._human_delay(1, 2)
                    break
            except:
                pass

        await self._dismiss_popups()
        await page.screenshot(path=str(_SCREENSHOTS_DIR / "login_page_state.png"))

        # Wait for login form with multiple selectors
        login_input = None
        for selector in ['input[name="username"]', 'input[aria-label*="username"]', 'input[type="text"]']:
            try:
                await page.wait_for_selector(selector, state="visible", timeout=10000)
                login_input = page.locator(selector).first
                log.info("Found login form with: %s", selector)
                break
            except:
                pass

        if not login_input:
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "login_form_not_found.png"))
            log.warning("Login form not found, checking page state...")
            if await self._is_logged_in():
                self._logged_in = True
                return
            raise RuntimeError("Login form not found")

        # Fill credentials
        await login_input.fill(self._username)
        await self._human_delay(0.5, 1)
        await page.locator('input[type="password"]').first.fill(self._password)
        await self._human_delay(0.5, 1)

        # Submit
        await page.keyboard.press("Enter")
        log.info("Waiting for login...")
        await self._human_delay(5, 8)
        await self._dismiss_popups()
        await self._human_delay(2, 3)
        await self._dismiss_popups()

        if await self._is_logged_in():
            self._logged_in = True
            await self._save_session()
            log.info("Logged in as @%s", self._username)
        else:
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "login_failed.png"))
            raise RuntimeError("Login failed")

    async def _click_create_button(self):
        """Click Create in sidebar, then Post from sub-menu."""
        page = self._page

        create_link = page.get_by_role("link", name="Create")
        if await create_link.count() > 0 and await create_link.first.is_visible():
            log.info("Clicking Create link...")
            await create_link.first.click()
        else:
            create_a = page.locator("a:has-text('Create')")
            clicked = False
            for i in range(await create_a.count()):
                if await create_a.nth(i).is_visible():
                    await create_a.nth(i).click()
                    clicked = True
                    break
            if not clicked:
                raise RuntimeError("Could not find Create button")

        await self._human_delay(1.5, 2.5)

        # Find Post in submenu
        async def find_submenu_item(text):
            candidates = page.get_by_text(text, exact=True)
            for i in range(await candidates.count()):
                el = candidates.nth(i)
                if not await el.is_visible():
                    continue
                try:
                    href = await el.evaluate("el => el.closest('a')?.getAttribute('href') || ''")
                    if href and '/' in href:
                        continue
                except:
                    pass
                return el
            return None

        el = await find_submenu_item("Post")
        if el:
            log.info("Clicking 'Post' in submenu...")
            await el.click()
            return

        el = await find_submenu_item("Reel")
        if el:
            log.info("Clicking 'Reel' in submenu...")
            await el.click()
            return

        if await page.locator("div[role='dialog']").count() > 0:
            log.info("Dialog opened directly")
            return

        await page.screenshot(path=str(_SCREENSHOTS_DIR / "submenu_fail.png"))
        raise RuntimeError("Could not find Post/Reel in submenu")

    async def _click_next(self):
        """Click Next button."""
        page = self._page
        btn = page.locator("div[role='button']:has-text('Next'), button:has-text('Next')").first
        await btn.wait_for(state="visible", timeout=30000)
        log.info("Clicking Next...")
        await btn.dispatch_event("click")

    async def _click_share(self):
        """Click Share button."""
        page = self._page
        share_btn = page.locator(
            "div[role='dialog'] div[role='button']:has-text('Share'), "
            "div[role='dialog'] button:has-text('Share')"
        )

        if await share_btn.count() == 0:
            raise RuntimeError("Share button not found")

        target = None
        for i in range(await share_btn.count()):
            try:
                text = await share_btn.nth(i).inner_text()
                if text.strip() == "Share" and await share_btn.nth(i).is_visible():
                    target = share_btn.nth(i)
                    break
            except:
                pass

        if not target:
            target = share_btn.first

        box = await target.bounding_box()
        if box:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            log.info("Clicking Share at (%.0f, %.0f)...", cx, cy)
            await page.mouse.click(cx, cy)
        else:
            await target.dispatch_event("click")

    async def _wait_for_video_processed(self, timeout=120):
        """Wait for video to be processed."""
        page = self._page
        start = time.time()
        while time.time() - start < timeout:
            try:
                btn = page.locator("div[role='button']:has-text('Next'), button:has-text('Next')")
                if await btn.count() > 0 and await btn.first.is_visible():
                    log.info("Video processed (%.0fs)", time.time() - start)
                    return True
            except:
                pass
            await asyncio.sleep(3)
        return False

    async def _wait_for_upload_complete(self, timeout=180):
        """Wait for upload confirmation."""
        page = self._page
        start = time.time()
        while time.time() - start < timeout:
            try:
                if await page.locator("img[alt*='checkmark']").count() > 0:
                    return True
                dialog = page.locator("div[role='dialog']")
                if await dialog.count() > 0:
                    text = (await dialog.first.inner_text()).lower()
                    if "shared" in text or "your reel" in text:
                        return True
                    if "sharing" in text:
                        log.info("Sharing in progress... (%.0fs)", time.time() - start)
            except:
                pass
            await asyncio.sleep(5)
        return False

    async def upload(self, video_path: str, caption: str = "") -> str:
        """Upload a reel to Instagram."""
        if not self._upload_enabled:
            return "upload_disabled"

        if not self._username or not self._password:
            return "error: no credentials"

        video = Path(video_path)
        if not video.exists():
            return f"error: file not found: {video_path}"

        try:
            result = await self._upload_impl(str(video.resolve()), caption)
            return result
        except Exception as exc:
            log.exception("Upload failed: %s", exc)
            return f"error: {exc}"

    async def _upload_impl(self, video_path: str, caption: str) -> str:
        """Async upload implementation."""
        log.info("Starting upload: %s", Path(video_path).name)

        try:
            # Step 1: Login
            await self._login()
            page = self._page
            await page.goto("https://www.instagram.com/", wait_until="load")
            await self._human_delay(3, 5)
            await self._dismiss_popups()
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "step1_logged_in.png"))

            # Step 2: Click Create -> Post
            await self._click_create_button()
            await self._human_delay(2, 3)
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "step2_dialog.png"))

            # Wait for dialog
            await page.wait_for_selector("div[role='dialog']", state="visible", timeout=10000)
            await self._human_delay(1, 2)

            # Step 3: Upload file
            log.info("Selecting video file...")
            file_input = page.locator("input[type='file']")
            if await file_input.count() > 0:
                await file_input.first.set_input_files(video_path)
            else:
                async with page.expect_file_chooser(timeout=15000) as fc:
                    select_btn = page.locator("button:has-text('Select from computer'), button:has-text('Select from')")
                    if await select_btn.count() > 0:
                        await select_btn.first.click()
                    else:
                        await page.locator("div[role='dialog'] button").last.click()
                await fc.value.set_files(video_path)

            log.info("Video selected, waiting for processing...")
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "step3_file_selected.png"))

            # Step 4: Wait for processing
            if not await self._wait_for_video_processed(timeout=120):
                await page.screenshot(path=str(_SCREENSHOTS_DIR / "step4_timeout.png"))
                raise RuntimeError("Video processing timeout")

            # Step 5: Click Next (crop -> edit)
            await self._click_next()
            await self._human_delay(3, 4)

            # Step 6: Click Next (edit -> caption)
            await self._click_next()
            await self._human_delay(3, 4)
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "step5_caption.png"))

            # Step 7: Write caption
            if caption:
                log.info("Writing caption...")
                caption_area = page.locator(
                    "div[aria-label*='caption'], div[contenteditable='true'][role='textbox']"
                )
                try:
                    await caption_area.first.wait_for(state="visible", timeout=10000)
                    await caption_area.first.click()
                    await self._human_delay(0.5, 1)
                    await page.keyboard.type(caption[:2200], delay=10)
                    log.info("Caption written")
                except Exception as e:
                    log.warning("Could not write caption: %s", e)

            await self._human_delay(1, 2)
            await page.screenshot(path=str(_SCREENSHOTS_DIR / "step6_before_share.png"))

            # Step 8: Share
            log.info("Publishing...")
            await self._click_share()
            await self._human_delay(3, 5)

            # Step 9: Wait for confirmation
            log.info("Waiting for upload confirmation...")
            if await self._wait_for_upload_complete(timeout=180):
                log.info("Upload successful!")
                await self._save_session()

                # Do some human-like actions after upload
                await self._human_delay(2, 4)
                await self._do_human_actions()

                # Close browser after upload
                await self._close_browser()
                return "uploaded"
            else:
                await page.screenshot(path=str(_SCREENSHOTS_DIR / "step7_uncertain.png"))
                log.warning("Upload uncertain")
                await self._close_browser()
                return "uncertain"

        except Exception as exc:
            try:
                await self._page.screenshot(path=str(_SCREENSHOTS_DIR / f"error_{int(time.time())}.png"))
            except:
                pass
            await self._close_browser()
            raise

    async def _do_human_actions(self):
        """Perform random human-like actions after upload."""
        page = self._page
        try:
            actions = [
                ("scroll_feed", self._scroll_feed),
                ("view_profile", self._view_own_profile),
                ("check_notifications", self._check_notifications),
                ("browse_explore", self._browse_explore),
            ]

            num_actions = random.randint(2, 3)
            selected = random.sample(actions, min(num_actions, len(actions)))

            for name, action in selected:
                try:
                    log.info("Human action: %s", name)
                    await action()
                    await self._human_delay(2, 5)
                except:
                    pass
        except:
            pass

    async def _scroll_feed(self):
        """Scroll through feed a bit."""
        page = self._page
        await page.goto("https://www.instagram.com/", wait_until="load")
        await self._human_delay(2, 3)
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(300, 600))
            await self._human_delay(1, 3)

    async def _view_own_profile(self):
        """Visit own profile briefly."""
        page = self._page
        await page.goto(f"https://www.instagram.com/{self._username}/", wait_until="load")
        await self._human_delay(2, 4)
        await page.mouse.wheel(0, random.randint(200, 400))
        await self._human_delay(1, 2)

    async def _check_notifications(self):
        """Click notifications icon and glance."""
        page = self._page
        try:
            notif = page.locator('svg[aria-label="Notifications"]')
            if await notif.count() > 0:
                await notif.first.click()
                await self._human_delay(2, 4)
                await page.keyboard.press("Escape")
        except:
            pass

    async def _browse_explore(self):
        """Visit explore page briefly."""
        page = self._page
        await page.goto("https://www.instagram.com/explore/", wait_until="load")
        await self._human_delay(2, 3)
        for _ in range(random.randint(1, 2)):
            await page.mouse.wheel(0, random.randint(200, 400))
            await self._human_delay(1, 2)

    async def _close_browser(self):
        """Close browser and reset state."""
        log.info("Closing browser...")
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None
        self._logged_in = False

    async def close(self):
        """Close browser."""
        await self._close_browser()
