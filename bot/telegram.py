"""Tiny Telegram client + message formatting."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from html import escape

import aiohttp

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 3900  # Telegram max is 4096 chars


def ago(dt) -> str:
    if not dt:
        return "date not listed"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} days ago"


def format_job(job: dict) -> str:
    loc = escape(job.get("location") or "Location not listed")
    if job.get("unclear"):
        loc += " <i>(country not stated — check)</i>"
    return (
        f"🎨 <b>{escape(job['title'])}</b>\n"
        f"🏢 {escape(job.get('company') or 'Unknown company')}\n"
        f"📍 {loc}\n"
        f"🕒 {ago(job.get('posted'))} · via {escape(job.get('source_label', ''))}\n"
        f"<a href=\"{escape(job.get('url', ''), quote=True)}\">Open &amp; apply →</a>"
    )


def format_line(job: dict) -> str:
    flag = " ❔" if job.get("unclear") else ""
    return (f"• <a href=\"{escape(job.get('url', ''), quote=True)}\">{escape(job['title'])}</a>"
            f" — {escape(job.get('company') or '')} <i>({escape(job.get('location') or '')})</i>{flag}")


def chunk_lines(header: str, lines: list[str]) -> list[str]:
    msgs, cur = [], header
    for line in lines:
        if len(cur) + len(line) + 1 > LIMIT:
            msgs.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        msgs.append(cur)
    return msgs


class Telegram:
    def __init__(self, session: aiohttp.ClientSession, token: str | None, chat_id: str | None,
                 dry_run: bool = False):
        self.session, self.token, self.chat_id, self.dry_run = session, token, chat_id, dry_run
        self.sent: list[str] = []

    async def _call(self, method, **params):
        url = API.format(token=self.token, method=method)
        async with self.session.post(url, json=params) as r:
            data = await r.json(content_type=None)
            if r.status == 429:
                wait = (data.get("parameters") or {}).get("retry_after", 5)
                await asyncio.sleep(wait + 1)
                return await self._call(method, **params)
            if not data.get("ok"):
                raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
            return data["result"]

    async def find_chat_id(self) -> str | None:
        """Use the most recent message someone sent to the bot."""
        updates = await self._call("getUpdates", limit=100)
        for u in reversed(updates):
            msg = u.get("message") or u.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if chat.get("id"):
                return str(chat["id"])
        return None

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        if self.dry_run:
            print("\n──── TELEGRAM (dry run) ────\n" + text)
            return True
        try:
            await self._call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML",
                             disable_web_page_preview=True)
            await asyncio.sleep(1.1)  # stay under Telegram's per-chat rate limit
            return True
        except Exception as e:
            print(f"!! Telegram send failed: {e}")
            return False


def env_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None


def env_chat():
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
