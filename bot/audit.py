"""One-off check: list every design-ish job and why it was kept or dropped.

Run:  python -m bot.audit   -> writes data/audit.json
"""
import asyncio
import json

import aiohttp

from . import filters, sources
from .monitor import CONCURRENCY, DATA, load_companies, load_config, save_json


def reason(job, cfg):
    if not filters.title_matches(job.get("title", ""), cfg):
        return "title"
    ok, unclear = filters.location_check(job, cfg)
    if ok:
        return "KEPT-unclear" if unclear else "KEPT"
    return "location"


async def main():
    cfg = load_config()
    companies = load_companies()
    rows = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                     connector=aiohttp.TCPConnector(limit=CONCURRENCY)) as s:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(label, fn):
            async with sem:
                try:
                    for j in await fn():
                        if filters.looks_design_related(j.get("title", "")):
                            rows.append({"src": label, "title": j["title"], "company": j.get("company"),
                                         "location": j.get("location"), "country": j.get("country"),
                                         "remote": j.get("remote"), "verdict": reason(j, cfg)})
                except Exception:
                    pass

        tasks = [one(ats, lambda a=ats, sl=slug: sources.fetch_ats(s, a, sl))
                 for ats, slugs in companies.items() for slug in slugs]
        tasks += [one(n, lambda n=n: sources.fetch_board(s, n)) for n in sources.BOARD_LABELS
                  if n in ("remotive", "remoteok", "weworkremotely", "himalayas")]
        await asyncio.gather(*tasks)
    save_json(DATA / "audit.json", rows)
    print(len(rows), "design-ish jobs written")


if __name__ == "__main__":
    asyncio.run(main())
