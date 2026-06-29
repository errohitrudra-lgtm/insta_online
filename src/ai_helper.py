"""
NVIDIA AI helper – optional enhancement.

Uses the NVIDIA API (Mistral model) to analyse errors, suggest fixes,
generate captions, etc.  The app runs perfectly fine without it.
If the AI goes down or the key is invalid, all calls gracefully return
fallback values.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import aiohttp

from .config import AISettings
from .logger import get_logger

log = get_logger("ai_helper")


class AIHelper:
    """Optional NVIDIA-backed AI assistant."""

    def __init__(self, settings: AISettings) -> None:
        self.settings = settings
        self._available = settings.enabled and bool(settings.api_key)
        if self._available:
            log.info("AI helper enabled (model: %s)", settings.model)
        else:
            log.info("AI helper disabled – app will run without AI support")

    async def ask(self, prompt: str, max_tokens: int = 1024) -> str:
        """Send a prompt to the AI and return the response text.

        Returns empty string on any failure – never raises.
        """
        if not self._available:
            return ""

        try:
            return await self._call_api(prompt, max_tokens)
        except Exception as exc:
            log.warning("AI call failed (non-critical): %s", exc)
            return ""

    async def analyse_error(self, error_msg: str, context: str = "") -> str:
        """Ask the AI to analyse a runtime error and suggest a fix."""
        prompt = (
            f"You are a Python debugging assistant for an Instagram monitoring app. "
            f"Analyse this error and suggest a concise fix:\n\n"
            f"Error: {error_msg}\n"
        )
        if context:
            prompt += f"Context: {context}\n"
        return await self.ask(prompt, max_tokens=512)

    async def generate_caption(self, original_caption: str) -> str:
        """Generate a rephrased caption for a reel (for uploading)."""
        if not original_caption:
            return ""
        prompt = (
            f"Rephrase this Instagram reel caption to be engaging and natural. "
            f"Keep relevant hashtags. Return only the new caption, nothing else.\n\n"
            f"Original: {original_caption}"
        )
        result = await self.ask(prompt, max_tokens=300)
        return result if result else original_caption

    async def _call_api(self, prompt: str, max_tokens: int) -> str:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.15,
            "top_p": 1.00,
            "stream": False,
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.settings.invoke_url, headers=headers, json=payload
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.warning("AI API returned %d: %s", resp.status, text[:200])
                    return ""
                data = await resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
