"""Weekly: find companies that use Greenhouse / Lever / Ashby / Workable / SmartRecruiters.

How: the public Common Crawl web index lists pages it has crawled. Every company
job board lives at a predictable address (e.g. jobs.lever.co/<company>), so we
read those addresses, pull out the company IDs, check each one is live, and save
the list to data/companies.json.

Run:  python -m bot.discover
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from . import sources
from .monitor import DATA, load_json, save_json
from .telegram import Telegram, env_chat, env_token

CC = "https://index.commoncrawl.org"
PATTERNS = {
    "greenhouse": ["boards.greenhouse.io/*", "job-boards.greenhouse.io/*"],
    "lever": ["jobs.lever.co/*"],
    "ashby": ["jobs.ashbyhq.com/*"],
    "workable": ["apply.workable.com/*"],
    "smartrecruiters": ["jobs.smartrecruiters.com/*"],
}
SKIP = {"", "embed", "jobs", "v1", "api", "j", "favicon.ico", "robots.txt", "static", "assets",
        "oneclick-ui", "sr-jobs", "careers", "search", "sitemap.xml", "_next", "cdn-cgi", "en", "login"}
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
MAX_PAGES = int(os.environ.get("CC_MAX_PAGES", "800"))       # per pattern per crawl
CRAWLS = int(os.environ.get("CC_CRAWLS", "4"))               # how many recent crawls to read
TIME_BUDGET = float(os.environ.get("DISCOVERY_MINUTES", "240")) * 60


def slug_from_url(ats: str, url: str) -> str | None:
    try:
        u = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    segs = [unquote(s) for s in u.path.split("/") if s]
    if ats == "greenhouse" and segs[:1] == ["embed"]:
        slug = (parse_qs(u.query).get("for") or [""])[0]
    else:
        slug = segs[0] if segs else ""
    if slug.lower() in SKIP or not SLUG_RE.match(slug):
        return None
    return slug if ats in ("ashby", "smartrecruiters") else slug.lower()


async def cc_get(session, url, tries=5):
    for i in range(tries):
        try:
            async with session.get(url) as r:
                if r.status == 200:
                    return await r.text()
                if r.status == 404:
                    return ""
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(5 * (i + 1))  # Common Crawl asks for gentle use
    return ""


async def crawl_candidates(session, deadline):
    info = json.loads(await cc_get(session, f"{CC}/collinfo.json") or "[]")
    crawls = [c["id"] for c in info[:CRAWLS]]
    print("Common Crawl collections:", crawls)
    found: dict[str, set[str]] = {k: set() for k in PATTERNS}
    for ats, pats in PATTERNS.items():
        for crawl in crawls:
            for pat in pats:
                base = f"{CC}/{crawl}-index?url={pat}&output=json&fl=url"
                meta = await cc_get(session, base + "&showNumPages=true")
                try:
                    pages = int(json.loads(meta.splitlines()[0]).get("pages", 0)) if meta else 0
                except Exception:
                    pages = 0
                pages = min(pages, MAX_PAGES)
                print(f"  {ats:16} {crawl} {pat}: {pages} pages")
                for p in range(pages):
                    if time.time() > deadline:
                        print("  ⏱  time budget reached, stopping crawl early")
                        return found
                    text = await cc_get(session, f"{base}&page={p}")
                    for line in text.splitlines():
                        try:
                            url = json.loads(line).get("url", "")
                        except Exception:
                            continue
                        s = slug_from_url(ats, url)
                        if s:
                            found[ats].add(s)
                    await asyncio.sleep(1)
        print(f"  → {ats}: {len(found[ats])} candidate companies so far")
    return found


async def validate(session, candidates: dict[str, set[str]], known: dict[str, set[str]]):
    sem = asyncio.Semaphore(30)
    live: dict[str, set[str]] = {k: set() for k in candidates}

    async def check(ats, slug):
        async with sem:
            try:
                jobs = await sources.fetch_ats(session, ats, slug)
                # SmartRecruiters answers "200, no jobs" even for made-up names
                if ats != "smartrecruiters" or jobs:
                    live[ats].add(slug)
            except Exception:
                pass

    todo = [(a, s) for a, ss in candidates.items() for s in ss if s.lower() not in known.get(a, set())]
    print(f"Validating {len(todo)} new candidates…")
    await asyncio.gather(*(check(a, s) for a, s in todo))
    return live


async def main():
    deadline = time.time() + TIME_BUDGET
    path = DATA / "companies.json"
    current = load_json(path, {})
    known = {a: {s.lower() for s in current.get(a, [])} for a in PATTERNS}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60),
                                     headers={"User-Agent": sources.UA}) as session:
        candidates = await crawl_candidates(session, deadline)
        live = await validate(session, candidates, known)

        added = 0
        for ats in PATTERNS:
            merged = {s.lower(): s for s in current.get(ats, [])}
            for s in live.get(ats, []):
                if s.lower() not in merged:
                    merged[s.lower()] = s
                    added += 1
            current[ats] = sorted(merged.values(), key=str.lower)
        current["_updated"] = time.strftime("%Y-%m-%d")
        save_json(path, current)
        total = sum(len(v) for k, v in current.items() if not k.startswith("_"))
        summary = f"🔎 Weekly company discovery: +{added:,} new companies. Now watching {total:,}."
        print(summary)

        state = load_json(DATA / "state.json", {})
        chat = env_chat() or state.get("chat_id")
        if env_token() and chat:
            await Telegram(session, env_token(), chat).send(summary)


if __name__ == "__main__":
    asyncio.run(main())
