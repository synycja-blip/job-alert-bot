"""Offline tests: fake API responses in the real formats, no network needed.

Run:  python -m pytest -q
"""
import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from bot import discover, filters, monitor, sources

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
NOW = datetime.now(timezone.utc)
iso = lambda d: d.isoformat().replace("+00:00", "Z")


# ── title filter ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("title,ok", [
    ("Graphic Designer", True),
    ("Senior Graphic Designer", True),
    ("Visual Designer, Brand", True),
    ("Presentation Designer (PowerPoint)", True),
    ("Marketing Designer II", True),
    ("Data Visualization Designer", True),
    ("Production Artist", True),
    ("Designer, Growth Marketing", True),
    ("Internal Communications Designer", True),   # "intern" must not hit "internal"
    ("Luxury Brand Designer", True),               # "ux" must not hit "luxury"
    ("Product Designer", False),
    ("Senior UX/UI Designer", False),
    ("UI Designer", False),
    ("Instructional Designer", False),
    ("Interior Designer", False),
    ("Design Engineer", False),
    ("ASIC Design Verification Engineer", False),
    ("Graphic Design Intern", False),
    ("Director of Brand Design", False),
    ("Software Engineer", False),
    ("Fashion Designer", False),
])
def test_titles(title, ok):
    assert filters.title_matches(title, CFG) is ok


def test_product_ux_toggle():
    assert filters.title_matches("Product Designer", {**CFG, "include_product_ux": True})


# ── location filter ────────────────────────────────────────────────────────
def J(location, remote=None, country=""):
    return {"title": "Graphic Designer", "location": location, "remote": remote, "country": country}


@pytest.mark.parametrize("job,ok,unclear", [
    (J("Remote - US"), True, False),
    (J("Remote, United States"), True, False),
    (J("United States (Remote)"), True, False),
    (J("Remote (USA)"), True, False),
    (J("Remote - Austin, TX"), True, False),
    (J("Remote - US or Canada"), True, False),
    (J("Remote - Worldwide"), True, False),
    (J("Remote"), True, True),
    (J("San Francisco, CA", remote=True), True, False),
    (J("Remote - UK"), False, False),
    (J("Remote, Canada"), False, False),
    (J("Remote - EMEA"), False, False),
    (J("Berlin, DE", remote=True), False, False),     # DE = Germany here, not Delaware
    (J("Bengaluru, India (Remote)"), False, False),
    (J("New York, NY"), False, False),                 # not remote
    (J("New York, NY", remote=False), False, False),
    (J("Hybrid - Seattle, WA"), False, False),
    (J("", remote=True, country="US"), True, False),
])
def test_locations(job, ok, unclear):
    assert filters.location_check(job, CFG) == (ok, unclear)


# ── parsers (real response shapes) ─────────────────────────────────────────
GH = {"jobs": [
    {"id": 1, "title": "Senior Graphic Designer", "location": {"name": "Remote - US"},
     "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "updated_at": iso(NOW),
     "first_published": iso(NOW - timedelta(hours=2)), "company_name": "Acme"},
    {"id": 2, "title": "Software Engineer", "location": {"name": "Remote - US"},
     "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "updated_at": iso(NOW)},
    {"id": 3, "title": "Graphic Designer", "location": {"name": "London, UK"},
     "absolute_url": "https://boards.greenhouse.io/acme/jobs/3", "updated_at": iso(NOW)},
]}
LEVER = [
    {"id": "abc", "text": "Visual Designer", "categories": {"location": "United States",
     "allLocations": ["United States"]}, "workplaceType": "remote", "country": "US",
     "hostedUrl": "https://jobs.lever.co/beta/abc", "createdAt": int(NOW.timestamp() * 1000)},
    {"id": "def", "text": "Visual Designer", "categories": {"location": "Chicago, IL"},
     "workplaceType": "hybrid", "country": "US", "hostedUrl": "https://jobs.lever.co/beta/def",
     "createdAt": int(NOW.timestamp() * 1000)},
]
ASHBY = {"jobs": [
    {"id": "u1", "title": "Brand Designer", "location": "Remote", "isRemote": True,
     "workplaceType": "Remote", "secondaryLocations": [{"location": "New York"}],
     "jobUrl": "https://jobs.ashbyhq.com/gamma/u1", "publishedAt": iso(NOW - timedelta(days=30)),
     "isListed": True, "address": {"postalAddress": {"addressCountry": "United States"}}},
]}
REMOTIVE = {"jobs": [
    {"id": 99, "url": "https://remotive.com/j/99", "title": "Senior Graphic Designer",
     "company_name": "Acme", "candidate_required_location": "USA",
     "publication_date": "2026-11-02T10:00:00"},   # same job as Greenhouse #1 → deduped
    {"id": 100, "url": "https://remotive.com/j/100", "title": "Presentation Designer",
     "company_name": "Deckly", "candidate_required_location": "USA", "publication_date": iso(NOW)},
]}
WWR = """<?xml version="1.0"?><rss><channel>
<item><title>Pixel Co: Marketing Designer</title><region>Anywhere in the World</region>
<link>https://weworkremotely.com/x</link><guid>wwr-1</guid><pubDate>Mon, 02 Nov 2026 12:00:00 +0000</pubDate></item>
<item><title>Euro Ltd: Graphic Designer</title><region>Europe Only</region>
<link>https://weworkremotely.com/y</link><guid>wwr-2</guid><pubDate>Mon, 02 Nov 2026 12:00:00 +0000</pubDate></item>
</channel></rss>"""
REMOTEOK = [{"legal": "notice"}, {"id": "7", "position": "Graphic Designer", "company": "Okay Inc",
            "location": "United States", "url": "https://remoteok.com/7", "epoch": int(NOW.timestamp())}]
SR = {"totalFound": 1, "content": [{"id": "s1", "name": "Graphic Designer", "releasedDate": iso(NOW),
      "company": {"name": "Visa"}, "location": {"city": "Austin", "region": "TX", "country": "us", "remote": True}}]}
WK = {"name": "Workco", "jobs": [{"title": "Graphic Designer", "shortcode": "W1", "country": "United States",
      "city": "Denver", "state": "Colorado", "telecommuting": True, "url": "https://apply.workable.com/workco/j/W1",
      "published_on": "2026-11-01"}]}
WD = {"total": 1, "jobPostings": [{"title": "Graphic Designer", "externalPath": "/job/Remote/Graphic-Designer_R1",
      "locationsText": "Remote - USA", "postedOn": "Posted Today"}]}


def test_parsers():
    g = sources.parse_greenhouse(GH, "acme")
    assert g[0]["company"] == "Acme" and g[0]["posted"] is not None
    l = sources.parse_lever(LEVER, "beta")
    assert l[0]["remote"] is True and l[1]["remote"] is False
    a = sources.parse_ashby(ASHBY, "gamma")
    assert a[0]["remote"] is True and "New York" in a[0]["location"]
    assert len(sources.parse_wwr(WWR)) == 2
    assert len(sources.parse_remoteok(REMOTEOK)) == 1
    s = sources.parse_smartrecruiters(SR, "Visa")
    assert filters.job_matches(s[0], CFG) == (True, False)
    w = sources.parse_workable(WK, "workco")
    assert filters.job_matches(w[0], CFG) == (True, False)
    site = sources.parse_workday_url("https://acme.wd5.myworkdayjobs.com/en-US/External")
    assert site["site"] == "External" and site["tenant"] == "acme"
    d = sources.parse_workday(WD, site)
    assert d[0]["url"].startswith("https://acme.wd5.myworkdayjobs.com/External/job/")
    assert filters.job_matches(d[0], CFG)[0]


def test_slug_extraction():
    f = discover.slug_from_url
    assert f("greenhouse", "https://boards.greenhouse.io/Airbnb/jobs/123") == "airbnb"
    assert f("greenhouse", "https://boards.greenhouse.io/embed/job_app?for=stripe&token=1") == "stripe"
    assert f("greenhouse", "https://boards.greenhouse.io/robots.txt") is None
    assert f("lever", "https://jobs.lever.co/palantir/abc-123") == "palantir"
    assert f("ashby", "https://jobs.ashbyhq.com/Ramp/xyz") == "Ramp"
    assert f("workable", "https://apply.workable.com/j/ABCD") is None


# ── full run (state, dedupe, first run, new-company rules) ─────────────────
class FakeTG:
    def __init__(self):
        self.sent = []
        self.chat_id, self.token = "1", "x"

    async def send(self, text):
        self.sent.append(text)
        return True


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "seed_companies.json").write_text(json.dumps({"greenhouse": ["acme"], "lever": ["beta"]}))
    (data / "companies.json").write_text(json.dumps({"ashby": ["gamma"]}))
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    monkeypatch.setattr(monitor, "DATA", data)
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setenv("DRY_RUN", "1")
    return data


def make_fetchers(gh=GH, ashby=ASHBY, extra_ats=None):
    async def ats(session, a, slug):
        table = {("greenhouse", "acme"): gh, ("lever", "beta"): LEVER, ("ashby", "gamma"): ashby}
        table.update(extra_ats or {})
        if (a, slug) not in table:
            raise sources.BoardGone(slug)
        return sources.ATS[a][1](table[(a, slug)], slug)

    async def board(session, name):
        return {"remotive": sources.parse_remotive(REMOTIVE), "weworkremotely": sources.parse_wwr(WWR),
                "remoteok": sources.parse_remoteok(REMOTEOK), "himalayas": []}[name]

    return {"ats": ats, "board": board}


def run(fetchers, tg):
    return asyncio.run(monitor.main(fetchers=fetchers, session=object(), tg=tg))


def test_full_flow(sandbox):
    # 1st run: digest of what's open now, no duplicates
    tg = FakeTG()
    alerts, current, stats = run(make_fetchers(), tg)
    titles = sorted(j["title"] + "@" + j["company"] for j in current)
    assert "Senior Graphic Designer@Acme" in titles
    assert titles.count("Senior Graphic Designer@Acme") == 1        # Greenhouse + Remotive deduped
    assert not any("Euro" in t for t in titles)                     # Europe-only dropped
    assert not any("Software" in t for t in titles)
    assert len(tg.sent) >= 1 and "Every remote design role open right now" in tg.sent[0]

    # 2nd run, nothing changed → silence
    tg = FakeTG()
    alerts, *_ = run(make_fetchers(), tg)
    assert alerts == [] and tg.sent == []

    # 3rd run: Acme posts a new designer job → single detailed alert
    gh2 = {"jobs": GH["jobs"] + [{"id": 4, "title": "Brand Designer", "location": {"name": "Remote (USA)"},
           "absolute_url": "https://boards.greenhouse.io/acme/jobs/4", "first_published": iso(NOW),
           "company_name": "Acme"}]}
    tg = FakeTG()
    alerts, *_ = run(make_fetchers(gh=gh2), tg)
    assert [a["title"] for a in alerts] == ["Brand Designer"]
    assert "Open &amp; apply" in tg.sent[0] and "Brand Designer" in tg.sent[0]

    # 4th run: a newly discovered company appears with one old + one fresh job
    comps = json.loads((sandbox / "companies.json").read_text())
    comps["greenhouse"] = ["newco"]
    (sandbox / "companies.json").write_text(json.dumps(comps))
    newco = {"jobs": [
        {"id": 10, "title": "Graphic Designer", "location": {"name": "Remote - US"}, "absolute_url": "u10",
         "first_published": iso(NOW - timedelta(days=40)), "company_name": "NewCo"},
        {"id": 11, "title": "Visual Designer", "location": {"name": "Remote - US"}, "absolute_url": "u11",
         "first_published": iso(NOW - timedelta(hours=3)), "company_name": "NewCo"},
    ]}
    state = json.loads((sandbox / "state.json").read_text())
    state["run_count"] = 0
    (sandbox / "state.json").write_text(json.dumps(state))
    tg = FakeTG()
    groups = CFG["cold_rotation_groups"]
    # run enough times that newco's rotation group comes up
    got = []
    for _ in range(groups):
        a, *_ = run(make_fetchers(gh=gh2, extra_ats={("greenhouse", "newco"): newco}), tg)
        got += a
    assert [a["title"] for a in got] == ["Visual Designer"]          # old posting stayed quiet

    # state file is valid JSON and tracks a dead board as dead
    st = json.loads((sandbox / "state.json").read_text())
    assert st["initialized"] and st["run_count"] >= 4


def test_dead_boards_skipped(sandbox):
    comps = {"greenhouse": ["ghost"]}
    (sandbox / "companies.json").write_text(json.dumps(comps))
    for _ in range(CFG["cold_rotation_groups"] * (monitor.DEAD_AFTER + 1)):
        run(make_fetchers(), FakeTG())
    st = json.loads((sandbox / "state.json").read_text())
    assert st["dead"]["greenhouse:ghost"] == monitor.DEAD_AFTER   # then never checked again
