"""Fetch and normalise jobs from hiring systems (ATS) and remote job boards.

Every parser returns a list of dicts with these keys:
  id, title, company, location, country, remote (True/False/None), url, posted (datetime|None)
"""
from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlparse

import aiohttp

UA = "Mozilla/5.0 (compatible; personal-job-alert-bot/1.0)"


class BoardGone(Exception):
    """The company board does not exist (404/410)."""


# ── helpers ────────────────────────────────────────────────────────────────

def _dt(value):
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1e12:  # milliseconds
                v /= 1000
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(value).strip()
        if s.isdigit():
            return _dt(int(s))
        s = s.replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return parsedate_to_datetime(str(value))
        except Exception:
            return None


def _pretty_slug(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


async def get_json(session: aiohttp.ClientSession, url: str, *, method="GET", payload=None,
                   retries: int = 2):
    last = None
    for attempt in range(retries + 1):
        try:
            async with session.request(method, url, json=payload,
                                       headers={"User-Agent": UA, "Accept": "application/json"}) as r:
                if r.status in (404, 410):
                    raise BoardGone(url)
                if r.status == 429 or r.status >= 500:
                    last = RuntimeError(f"HTTP {r.status}")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                text = await r.text()
                return json.loads(text)
        except BoardGone:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            last = e
            await asyncio.sleep(1 + attempt)
    raise last or RuntimeError("request failed")


async def get_text(session, url):
    async with session.get(url, headers={"User-Agent": UA}) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return await r.text()


# ── ATS parsers ────────────────────────────────────────────────────────────

def parse_greenhouse(data, slug):
    out = []
    for j in (data or {}).get("jobs", []):
        out.append({
            "id": str(j.get("id")),
            "title": j.get("title", ""),
            "company": j.get("company_name") or _pretty_slug(slug),
            "location": (j.get("location") or {}).get("name", ""),
            "country": "",
            "remote": None,
            "url": j.get("absolute_url", ""),
            "posted": _dt(j.get("first_published") or j.get("updated_at")),
        })
    return out


def parse_lever(data, slug):
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or []
        loc = cats.get("location") or ""
        if locs:
            loc = " / ".join(dict.fromkeys([loc] + list(locs))) if loc else " / ".join(locs)
        wt = (j.get("workplaceType") or "").lower()
        remote = True if wt == "remote" else False if wt in ("hybrid", "on-site", "onsite") else None
        out.append({
            "id": str(j.get("id")),
            "title": j.get("text", ""),
            "company": _pretty_slug(slug),
            "location": loc,
            "country": j.get("country") or "",
            "remote": remote,
            "url": j.get("hostedUrl", ""),
            "posted": _dt(j.get("createdAt")),
        })
    return out


def parse_ashby(data, slug):
    out = []
    for j in (data or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        wt = (j.get("workplaceType") or "").lower()
        remote = True if (j.get("isRemote") or wt == "remote") else False if wt in ("hybrid", "onsite") else None
        out.append({
            "id": str(j.get("id")),
            "title": j.get("title", ""),
            "company": _pretty_slug(slug),
            "location": " / ".join(dict.fromkeys(l for l in locs if l)),
            "country": country,
            "remote": remote,
            "url": j.get("jobUrl") or j.get("applyUrl") or "",
            "posted": _dt(j.get("publishedAt")),
        })
    return out


def parse_workable(data, slug):
    out = []
    company = (data or {}).get("name") or _pretty_slug(slug)
    for j in (data or {}).get("jobs", []):
        parts = [j.get("city"), j.get("state"), j.get("country")]
        loc = ", ".join(p for p in parts if p)
        if j.get("telecommuting"):
            loc = ("Remote - " + loc) if loc else "Remote"
        out.append({
            "id": str(j.get("shortcode") or j.get("id")),
            "title": j.get("title", ""),
            "company": company,
            "location": loc,
            "country": j.get("country") or "",
            "remote": True if j.get("telecommuting") else None,
            "url": j.get("url") or j.get("shortlink") or "",
            "posted": _dt(j.get("published_on") or j.get("created_at")),
        })
    return out


def parse_smartrecruiters(data, slug):
    out = []
    for j in (data or {}).get("content", []):
        loc = j.get("location") or {}
        country = (loc.get("country") or "").upper()
        full = loc.get("fullLocation") or ", ".join(p for p in [loc.get("city"), loc.get("region"), country] if p)
        if loc.get("remote"):
            full = "Remote - " + full if full else "Remote"
        out.append({
            "id": str(j.get("id")),
            "title": j.get("name", ""),
            "company": (j.get("company") or {}).get("name") or _pretty_slug(slug),
            "location": full,
            "country": "United States" if country == "US" else country,
            "remote": True if loc.get("remote") else None,
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
            "posted": _dt(j.get("releasedDate")),
        })
    return out


def _workday_posted(text):
    t = (text or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return now - timedelta(days=int(m.group(1)))
    return None


def parse_workday(data, site):
    out = []
    host, tenant, name = site["host"], site["tenant"], site["site"]
    for j in (data or {}).get("jobPostings", []):
        path = j.get("externalPath", "")
        loc = j.get("locationsText", "") or ""
        remote_type = (j.get("remoteType") or "").lower()
        if "remote" in remote_type and "remote" not in loc.lower():
            loc = "Remote - " + loc
        out.append({
            "id": path,
            "title": j.get("title", ""),
            "company": _pretty_slug(tenant),
            "location": loc,
            "country": "",
            "remote": True if "remote" in remote_type else None,
            "url": f"https://{host}/{name}{path}",
            "posted": _workday_posted(j.get("postedOn")),
        })
    return out


def parse_workday_url(url: str):
    """https://acme.wd5.myworkdayjobs.com/en-US/External -> parts for the CXS API."""
    u = urlparse(url.strip())
    host = u.netloc
    tenant = host.split(".")[0]
    segs = [s for s in u.path.split("/") if s]
    segs = [s for s in segs if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", s)]
    if not segs:
        return None
    return {"host": host, "tenant": tenant, "site": segs[0], "key": f"workday:{host}/{segs[0]}"}


ATS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", parse_greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", parse_ashby),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{slug}", parse_workable),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}",
                        parse_smartrecruiters),
}


async def fetch_ats(session, ats: str, slug: str):
    url_t, parser = ATS[ats]
    if ats == "smartrecruiters":
        jobs, offset = [], 0
        for _ in range(10):
            data = await get_json(session, url_t.format(slug=slug, offset=offset))
            batch = parser(data, slug)
            jobs += batch
            offset += 100
            if offset >= (data or {}).get("totalFound", 0) or not batch:
                break
        return jobs
    data = await get_json(session, url_t.format(slug=slug))
    return parser(data, slug)


async def fetch_workday(session, site: dict):
    api = f"https://{site['host']}/wday/cxs/{site['tenant']}/{site['site']}/jobs"
    jobs, offset = [], 0
    for _ in range(5):
        data = await get_json(session, api, method="POST",
                              payload={"appliedFacets": {}, "limit": 20, "offset": offset,
                                       "searchText": "designer"})
        batch = parse_workday(data, site)
        jobs += batch
        offset += 20
        if offset >= (data or {}).get("total", 0) or not batch:
            break
    return jobs


# ── Remote job boards ──────────────────────────────────────────────────────

def parse_remotive(data):
    out = []
    for j in (data or {}).get("jobs", []):
        out.append({
            "id": str(j.get("id")), "title": j.get("title", ""), "company": j.get("company_name", ""),
            "location": "Remote - " + (j.get("candidate_required_location") or ""),
            "country": "", "remote": True, "url": j.get("url", ""),
            "posted": _dt(j.get("publication_date")),
        })
    return out


def parse_remoteok(data):
    out = []
    for j in data or []:
        if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
            continue  # first element is a legal notice
        out.append({
            "id": str(j.get("id")), "title": unescape(j.get("position", "")),
            "company": unescape(j.get("company", "")),
            "location": "Remote - " + (j.get("location") or ""),
            "country": "", "remote": True, "url": j.get("url") or j.get("apply_url") or "",
            "posted": _dt(j.get("epoch") or j.get("date")),
        })
    return out


def parse_wwr(xml_text):
    out = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        raw = (item.findtext("title") or "").strip()
        company, _, title = raw.partition(":")
        if not title:
            company, title = "", raw
        region = item.findtext("region") or ""
        link = (item.findtext("link") or "").strip()
        out.append({
            "id": item.findtext("guid") or link, "title": unescape(title.strip()),
            "company": unescape(company.strip()),
            "location": "Remote - " + region, "country": "", "remote": True, "url": link,
            "posted": _dt(item.findtext("pubDate")),
        })
    return out


def parse_himalayas(data):
    out = []
    for j in (data or {}).get("jobs", []):
        restr = j.get("locationRestrictions") or []
        restr = [r if isinstance(r, str) else (r.get("name") or "") for r in restr]
        out.append({
            "id": str(j.get("guid") or j.get("applicationLink")), "title": j.get("title", ""),
            "company": j.get("companyName", ""),
            "location": "Remote - " + (", ".join(restr) if restr else "Worldwide"),
            "country": "", "remote": True,
            "url": j.get("applicationLink") or j.get("guid") or "",
            "posted": _dt(j.get("pubDate")),
        })
    return out


async def fetch_board(session, name: str):
    if name == "remotive":
        return parse_remotive(await get_json(session, "https://remotive.com/api/remote-jobs?category=design"))
    if name == "remoteok":
        return parse_remoteok(await get_json(session, "https://remoteok.com/api?tag=design"))
    if name == "weworkremotely":
        return parse_wwr(await get_text(session, "https://weworkremotely.com/categories/remote-design-jobs.rss"))
    if name == "himalayas":
        jobs = []
        for offset in (0, 100, 200):
            jobs += parse_himalayas(await get_json(session, f"https://himalayas.app/jobs/api?limit=100&offset={offset}"))
        return jobs
    raise ValueError(name)


BOARD_LABELS = {
    "remotive": "Remotive", "remoteok": "Remote OK", "weworkremotely": "We Work Remotely",
    "himalayas": "Himalayas", "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "workable": "Workable", "smartrecruiters": "SmartRecruiters", "workday": "Workday",
}
