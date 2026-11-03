"""Decide whether a job is a remote, US-eligible graphic/visual design role."""
from __future__ import annotations

import re
from functools import lru_cache

PRODUCT_UX_TERMS = [
    "product designer", "product design", "ux", "ui", "user experience",
    "user interface", "interaction design",
]


@lru_cache(maxsize=4096)
def _term_regex(term: str) -> re.Pattern:
    term = term.strip().lower()
    # whole-word match; allow simple plural / -er endings ("design" -> "designer(s)")
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?:s|er|ers)?(?![a-z0-9])")


def _has_any(text: str, terms) -> bool:
    text = text.lower()
    return any(_term_regex(t).search(text) for t in terms if t and t.strip())


BROAD_DESIGN = ["design", "designer", "illustrator", "production artist", "infographic", "creative"]


def looks_design_related(title: str) -> bool:
    """Loose check used to mark companies as 'hot' (they hire designers)."""
    return _has_any(title or "", BROAD_DESIGN)


def title_matches(title: str, cfg: dict) -> bool:
    if not title:
        return False
    include = cfg.get("title_include", [])
    exclude = list(cfg.get("title_exclude", []))
    if cfg.get("include_product_ux"):
        exclude = [t for t in exclude if t.strip().lower() not in PRODUCT_UX_TERMS]
        include = list(include) + ["product designer", "ux designer", "ui designer"]
    if _has_any(title, cfg.get("title_always_include", [])) and not _has_any(title, ["intern", "internship"]):
        return True
    return _has_any(title, include) and not _has_any(title, exclude)


# ── Location ────────────────────────────────────────────────────────────────

US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
]
US_CITIES = [
    "san francisco", "los angeles", "seattle", "chicago", "boston", "austin", "denver",
    "atlanta", "miami", "minneapolis", "las vegas", "portland", "san diego", "dallas",
    "houston", "phoenix", "philadelphia", "nashville", "salt lake city", "raleigh",
    "brooklyn", "manhattan", "nyc", "sf bay area", "bay area", "silicon valley",
]
US_GENERIC = [
    "united states", "usa", "us", "u.s", "u.s.", "u.s.a", "u.s.a.", "america", "americas",
    "north america", "nationwide", "us-based", "us based", "amer",
    # open to everyone → US people can apply
    "worldwide", "anywhere", "global", "globally",
]
STATE_CODES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_STATE_CODE_RE = re.compile(r"(?:,|-|\(|\s)\s*(?:" + "|".join(STATE_CODES) + r")\b")

NON_US = [
    "canada", "toronto", "vancouver", "montreal", "ottawa", "calgary",
    "uk", "united kingdom", "england", "scotland", "london", "manchester", "ireland", "dublin",
    "germany", "berlin", "munich", "hamburg", "france", "paris", "spain", "madrid", "barcelona",
    "portugal", "lisbon", "netherlands", "amsterdam", "belgium", "switzerland", "zurich",
    "austria", "vienna", "italy", "milan", "rome", "poland", "warsaw", "krakow", "ukraine",
    "kyiv", "romania", "bulgaria", "serbia", "croatia", "czech", "prague", "hungary",
    "budapest", "greece", "turkey", "istanbul", "sweden", "stockholm", "denmark",
    "copenhagen", "norway", "oslo", "finland", "helsinki", "estonia", "latvia", "lithuania",
    "europe", "european", "eu", "emea", "apac", "apj", "asia", "latam", "latin america",
    "south america", "mena", "middle east", "africa",
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "chennai",
    "pakistan", "bangladesh", "sri lanka", "nepal",
    "singapore", "malaysia", "indonesia", "philippines", "manila", "vietnam", "thailand",
    "japan", "tokyo", "korea", "seoul", "china", "shanghai", "beijing", "hong kong", "taiwan",
    "australia", "sydney", "melbourne", "new zealand", "auckland",
    "brazil", "são paulo", "sao paulo", "mexico", "mexico city", "argentina", "buenos aires",
    "colombia", "bogota", "chile", "peru", "uruguay", "costa rica",
    "israel", "tel aviv", "dubai", "uae", "saudi", "qatar", "egypt", "nigeria", "lagos",
    "kenya", "nairobi", "south africa", "cape town",
]
US_WIDE_ONLY = {"united states", "us", "usa", "u.s.", "united states of america", "us-national",
                "united states | us", "united states | united states"}
REMOTE_WORDS = ["remote", "anywhere", "work from home", "wfh", "distributed", "telecommute",
                "virtual", "home based", "home-based", "fully remote"]


def _has_us_marker(text: str) -> bool:
    return _has_any(text, US_GENERIC + US_STATES + US_CITIES)


def _has_state_code(text: str) -> bool:
    return bool(_STATE_CODE_RE.search(" " + text))  # case-sensitive, e.g. "Austin, TX"


def location_check(job: dict, cfg: dict) -> tuple[bool, bool]:
    """Return (accepted, unclear_location)."""
    loc = " | ".join(x for x in [job.get("location") or "", job.get("country") or ""] if x)
    low = loc.lower()
    remote_flag = job.get("remote")

    us_wide = low.strip(" |") in US_WIDE_ONLY or low.replace(" ", "") in ("united states|us", "us|us", "us|united states")
    if cfg.get("remote_only", True) and us_wide and remote_flag is None and cfg.get("allow_us_wide", True):
        return True, True  # "United States" with no city: usually a remote role

    if cfg.get("remote_only", True):
        textual_remote = _has_any(low, REMOTE_WORDS)
        if remote_flag is False and not textual_remote:
            return False, False
        if not (remote_flag is True or textual_remote):
            return False, False
        if "hybrid" in low and remote_flag is not True and "remote" not in low:
            return False, False

    if not cfg.get("us_only", True):
        return True, False

    if _has_us_marker(loc):
        return True, False
    if _has_any(low, NON_US):
        return False, False
    if _has_state_code(loc):
        return True, False
    # nothing tells us the country
    if cfg.get("allow_unclear_remote", True):
        return True, True
    return False, False


def job_matches(job: dict, cfg: dict) -> tuple[bool, bool]:
    if not title_matches(job.get("title", ""), cfg):
        return False, False
    return location_check(job, cfg)


def fingerprint(job: dict) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return "fp:" + norm(job.get("company")) + "|" + norm(job.get("title"))
