"""Telegram alert sender — thin async wrapper, no retry storms."""
import asyncio
import logging
import os
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_BASE = f"https://api.telegram.org/bot{_TOKEN}"

Level = Literal["info", "warning", "critical"]

_EMOJI = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}


async def send(message: str, level: Level = "info") -> bool:
    if not _TOKEN or not _CHAT_ID:
        logger.warning("Telegram not configured, skipping alert")
        return False
    prefix = _EMOJI.get(level, "")
    text = f"{prefix} *{level.upper()}*\n{message}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_BASE}/sendMessage",
                json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"},
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Failed to send Telegram alert: %s", exc)
        return False


def send_sync(message: str, level: Level = "info") -> bool:
    return asyncio.run(send(message, level))
