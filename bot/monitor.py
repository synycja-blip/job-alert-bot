"""Main run: check every source once, send new matching jobs to Telegram, save state.

Run:  python -m bot.monitor           (needs TELEGRAM_BOT_TOKEN)
      DRY_RUN=1 python -m bot.monitor (prints instead of sending)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import yaml

from . import sources
from .filters import fingerprint, job_matches, looks_design_related
from .telegram import Telegram, chunk_lines, env_chat, env_token, format_job, format_line

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NOW = lambda: datetime.now(timezone.utc)

SEEN_DAYS = 120          # forget alerted jobs after this
HOT_DAYS = 180           # company counts as "hot" this long after a design-ish posting
DEAD_AFTER = 3           # 404s in a row before a board is skipped
CONCURRENCY = 40


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    tmp.replace(path)


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text()) or {}


def load_companies() -> dict[str, list[str]]:
    """Merge seed list + auto-discovered list -> {ats: [slugs]}."""
    merged: dict[str, dict[str, str]] = {}
    for f in ("seed_companies.json", "companies.json"):
        for ats, slugs in load_json(DATA / f, {}).items():
            if ats.startswith("_") or ats not in sources.ATS:
                continue
            bucket = merged.setdefault(ats, {})
            for s in slugs:
                bucket.setdefault(s.lower(), s)
    return {ats: sorted(b.values()) for ats, b in merged.items()}


def new_state():
    return {"initialized": False, "seen": {}, "baselined": [], "hot": {}, "dead": {},
            "run_count": 0, "chat_id": None, "last_run": None, "last_stats": {}}


def pick_boards(companies, seeds, state, cfg):
    """Hot + seeded companies every run; the rest in rotating groups."""
    groups = max(1, int(cfg.get("cold_rotation_groups", 4)))
    turn = state["run_count"] % groups
    hot_cut = NOW() - timedelta(days=HOT_DAYS)
    first_ever = not state["initialized"]
    picked = []
    for ats, slugs in companies.items():
        seed_set = {s.lower() for s in seeds.get(ats, [])}
        for slug in slugs:
            key = f"{ats}:{slug.lower()}"
            if state["dead"].get(key, 0) >= DEAD_AFTER:
                continue
            hot_at = state["hot"].get(key)
            is_hot = slug.lower() in seed_set or (hot_at and datetime.fromisoformat(hot_at) > hot_cut)
            if first_ever or is_hot or zlib.crc32(key.encode()) % groups == turn:
                picked.append((ats, slug, key))
    return picked


async def gather_jobs(session, cfg, boards, workday_sites, board_names, fetchers=None):
    """Returns list of (board_key, source_label, jobs|None, error|None)."""
    fetch_ats = (fetchers or {}).get("ats", sources.fetch_ats)
    fetch_board = (fetchers or {}).get("board", sources.fetch_board)
    fetch_workday = (fetchers or {}).get("workday", sources.fetch_workday)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def run(key, label, coro_fn):
        async with sem:
            try:
                return key, label, await coro_fn(), None
            except sources.BoardGone as e:
                return key, label, None, e
            except Exception as e:  # network hiccup, bad JSON, etc.
                return key, label, None, e

    tasks = [run(key, sources.BOARD_LABELS[ats], lambda a=ats, s=slug: fetch_ats(session, a, s))
             for ats, slug, key in boards]
    tasks += [run(site["key"], "Workday", lambda st=site: fetch_workday(session, st)) for site in workday_sites]
    tasks += [run(f"board:{n}", sources.BOARD_LABELS[n], lambda n=n: fetch_board(session, n)) for n in board_names]
    return await asyncio.gather(*tasks)


def process(results, state, cfg):
    """Update state; return (alerts, first_run_jobs, stats)."""
    now = NOW()
    seen = state["seen"]
    baselined = set(state["baselined"])
    first_ever = not state["initialized"]
    lookback = timedelta(hours=float(cfg.get("new_company_lookback_hours", 48)))
    alerts, current, stats = [], [], {"sources_ok": 0, "sources_failed": 0, "jobs_scanned": 0, "matches": 0}

    for key, label, jobs, err in results:
        if err is not None:
            stats["sources_failed"] += 1
            if isinstance(err, sources.BoardGone):
                state["dead"][key] = state["dead"].get(key, 0) + 1
            continue
        stats["sources_ok"] += 1
        state["dead"].pop(key, None)
        stats["jobs_scanned"] += len(jobs)
        board_known = key in baselined

        if not key.startswith("board:") and any(looks_design_related(j.get("title", "")) for j in jobs):
            state["hot"][key] = now.isoformat()

        for job in jobs:
            ok, unclear = job_matches(job, cfg)
            if not ok:
                continue
            stats["matches"] += 1
            job_key = f"{key}:{job['id']}"
            fp = fingerprint(job)
            if job_key in seen:
                continue
            job.update(unclear=unclear, source_label=label, _key=job_key, _fp=fp)
            duplicate = fp in seen  # same title+company already sent from another source
            if first_ever:
                if not duplicate:
                    current.append(job)
            elif duplicate:
                pass
            elif not board_known:
                # newly added company/board: only alert on fresh postings
                if job.get("posted") and now - job["posted"] <= lookback:
                    alerts.append(job)
            else:
                alerts.append(job)
            seen[job_key] = now.isoformat()
            seen[fp] = now.isoformat()
        baselined.add(key)

    state["baselined"] = sorted(baselined)
    # forget very old entries
    cutoff = now - timedelta(days=SEEN_DAYS)
    state["seen"] = {k: v for k, v in seen.items() if datetime.fromisoformat(v) > cutoff}
    return alerts, current, stats


def _sort_newest(jobs):
    far = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return sorted(jobs, key=lambda j: j.get("posted") or far, reverse=True)


async def notify(tg: Telegram, alerts, current, state, cfg, n_companies) -> list[dict]:
    """Send messages; returns jobs that FAILED to send (so they retry next run)."""
    failed = []
    if not state["initialized"]:
        current = _sort_newest(current)
        intro = (f"📋 <b>Every remote design role open right now</b>\nWatching {n_companies:,} company "
                 f"career pages + remote job boards. After this list you'll only get NEW roles.\n")
        if cfg.get("first_run_send_current", True) and current:
            cap = int(cfg.get("first_run_max_jobs", 40))
            intro += f"\nHere are {min(cap, len(current))} remote design roles open right now"
            intro += f" (of {len(current)} found):" if len(current) > cap else ":"
            for msg in chunk_lines(intro, [format_line(j) for j in current[:cap]]):
                await tg.send(msg)
        else:
            intro += "\nFrom now on you'll get a message only when a NEW matching role appears."
            await tg.send(intro)
        return failed

    alerts = _sort_newest(alerts)
    cap = int(cfg.get("max_alerts_per_run", 60))
    overflow = alerts[cap:]
    alerts = alerts[:cap]
    if len(alerts) <= 8:
        for j in alerts:
            if not await tg.send(format_job(j)):
                failed.append(j)
    else:
        header = f"🆕 <b>{len(alerts)} new remote design roles</b>"
        msgs = chunk_lines(header, [format_line(j) for j in alerts])
        for m in msgs:
            if not await tg.send(m):
                failed = alerts  # be safe: retry all next time
                break
    if overflow:
        await tg.send(f"…and {len(overflow)} more. Tighten title_include in config.yaml if this is too many.")
    return failed


async def main(fetchers=None, session=None, tg=None):
    t0 = time.time()
    cfg = load_config()
    state = {**new_state(), **load_json(DATA / "state.json", {})}
    seeds = load_json(DATA / "seed_companies.json", {})
    companies = load_companies()
    n_companies = sum(len(v) for v in companies.values()) + len(cfg.get("workday_sites") or [])
    dry = os.environ.get("DRY_RUN") == "1"

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                        connector=aiohttp.TCPConnector(limit=CONCURRENCY))
    try:
        if tg is None:
            tg = Telegram(session, env_token(), env_chat() or state.get("chat_id"), dry_run=dry)
        if not dry:
            if not tg.token:
                sys.exit("❌ TELEGRAM_BOT_TOKEN is missing. Add it under Settings → Secrets and variables → Actions.")
            if not tg.chat_id:
                tg.chat_id = await tg.find_chat_id()
                if not tg.chat_id:
                    sys.exit("❌ I can't find your chat yet. Open your bot in Telegram, press Start "
                             "(or send it any message), then run this workflow again.")
                state["chat_id"] = tg.chat_id
                await tg.send("✅ Connected! I'll send remote graphic design jobs here.")

        boards = pick_boards(companies, seeds, state, cfg)
        workday_sites = [s for s in (sources.parse_workday_url(u) for u in cfg.get("workday_sites") or []) if s]
        board_names = [n for n, on in (cfg.get("boards") or {}).items() if on and n in sources.BOARD_LABELS]
        print(f"Checking {len(boards)} company boards, {len(workday_sites)} Workday sites, "
              f"{len(board_names)} job boards…")

        results = await gather_jobs(session, cfg, boards, workday_sites, board_names, fetchers)
        alerts, current, stats = process(results, state, cfg)
        failed = await notify(tg, alerts, current, state, cfg, n_companies)
        for j in failed:  # let them retry next run
            state["seen"].pop(j["_key"], None)
            state["seen"].pop(j["_fp"], None)

        state["initialized"] = True
        state["run_count"] += 1
        state["last_run"] = NOW().isoformat()
        stats.update(new_alerts=len(alerts) - len(failed), seconds=round(time.time() - t0, 1),
                     boards_checked=len(boards))
        state["last_stats"] = stats
        save_json(DATA / "state.json", state)
        print("Done:", json.dumps(stats))
        return alerts, current, stats
    finally:
        if own_session:
            await session.close()


if __name__ == "__main__":
    asyncio.run(main())
