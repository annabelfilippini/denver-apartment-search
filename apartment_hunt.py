"""Apartment hunt: pull SF rentals from Craigslist + Exa, dedupe, deliver digest.

Run modes:
  python apartment_hunt.py           # full run: scrape + write markdown archive
  python apartment_hunt.py --dry     # scrape + write markdown, skip seen-set update
  python apartment_hunt.py --reset   # clear seen-set (re-shows everything next run)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlencode, urlparse, urlunparse

import requests
from dotenv import load_dotenv

import profiles

# ---------- config ----------

ROOT = Path(__file__).resolve().parent

# Generic, city-agnostic constants ------------------------------------------ #

# Domains we explicitly DON'T want to whitelist for the open-web pass. Exa
# indexes well across the rental web — whitelisting starves it. Just let it
# loose and filter on URL shape after.
REDDIT_DOMAINS = ["reddit.com"]

# Major aggregators that gate their search pages with anti-bot challenges. We
# can't direct-scrape them, but Exa often indexes their detail pages. Target
# each domain individually so Exa returns deep listing URLs we'd otherwise miss.
# City-agnostic: the same aggregators cover every US metro.
AGGREGATOR_DOMAINS = [
    "apartments.com", "apartmentfinder.com", "apartmentguide.com",
    "apartmenthomeliving.com", "apartmentlist.com", "avaloncommunities.com",
    "compass.com", "craigslist.org", "equityapartments.com", "forrent.com",
    "homefinder.com", "hotpads.com", "lovely.com", "padmapper.com",
    "redfin.com", "realtor.com", "rent.com", "rentable.co", "rentberry.com",
    "rentcafe.com", "renthop.com", "rentlingo.com", "rentometer.com",
    "rents.com", "trulia.com", "zillow.com", "zumper.com",
]

# Craigslist hard-blocks Python User-Agents (and their RSS feed) but serves HTML
# fine to a normal browser UA. Use a Safari string.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# Active-profile globals ----------------------------------------------------- #
#
# The rest of the pipeline reads these module-level names. `apply_profile()`
# rebinds them from a SearchProfile (see profiles.py) before a run, so swapping
# cities is a one-line change at the top of main(). SF is applied at import so
# anything importing these constants (e.g. build_html_digest) just works.

ACTIVE_PROFILE: profiles.SearchProfile

CITY_LABEL: str
STATE_LABEL: str
MIN_PRICE: int
IDEAL_MAX_PRICE: int
MAX_PRICE: int
MIN_BEDS: int
MAX_BEDS: int
NUM_PEOPLE: int
MOVE_BY: dt.date | None
NEIGHBORHOODS: list[str]
PREFERRED_NEIGHBORHOODS: list[str]
FALLBACK_NEIGHBORHOODS: list[str]
_CL_LOCATION_TO_HOOD: dict[str, str]
REQUIRE_NEIGHBORHOOD_MATCH: bool
TARGET_ZIPS: dict[str, str]
CITY_ZIP_PREFIXES: tuple[str, ...]
REGION_ZIP_RE: "re.Pattern[str]"
CRAIGSLIST_BASE: str
DIRECT_SOURCE_SEEDS: list[tuple[str, str]]
FIRECRAWL_SEEDS: list[tuple[str, str]]
PROPERTY_MANAGER_DOMAINS: list[str]
ZILLOW_RENTAL_URL: str
ZILLOW_SEARCH_TERM: str
ZILLOW_MAP_BOUNDS: dict
EXA_NEIGHBORHOODS: list[str]
BLOCKED_LOCATION_RE: "re.Pattern[str] | None"
LISTING_NOUN: str
MIN_BATHROOMS: int
ENRICH_DETAILS: bool
CRAIGSLIST_EXTRA_PARAMS: tuple
PREFERRED_KEYWORDS: tuple
SEEN_PATH: Path
DIGEST_PATH: Path
ARCHIVE_DIR: Path


def apply_profile(profile: profiles.SearchProfile) -> None:
    """Bind the active-profile globals from a SearchProfile."""
    global ACTIVE_PROFILE, CITY_LABEL, STATE_LABEL
    global MIN_PRICE, IDEAL_MAX_PRICE, MAX_PRICE, MIN_BEDS, MAX_BEDS, NUM_PEOPLE, MOVE_BY
    global NEIGHBORHOODS, PREFERRED_NEIGHBORHOODS, FALLBACK_NEIGHBORHOODS
    global _CL_LOCATION_TO_HOOD, REQUIRE_NEIGHBORHOOD_MATCH
    global TARGET_ZIPS, CITY_ZIP_PREFIXES, REGION_ZIP_RE
    global CRAIGSLIST_BASE, DIRECT_SOURCE_SEEDS, FIRECRAWL_SEEDS, PROPERTY_MANAGER_DOMAINS
    global ZILLOW_RENTAL_URL, ZILLOW_SEARCH_TERM, ZILLOW_MAP_BOUNDS, EXA_NEIGHBORHOODS, REDDIT_SUBREDDITS
    global BLOCKED_LOCATION_RE, LISTING_NOUN, MIN_BATHROOMS, ENRICH_DETAILS
    global CRAIGSLIST_EXTRA_PARAMS, PREFERRED_KEYWORDS
    global SEEN_PATH, DIGEST_PATH, ARCHIVE_DIR

    ACTIVE_PROFILE = profile
    CITY_LABEL = profile.city_label
    STATE_LABEL = profile.state_label
    MIN_PRICE = profile.min_price
    IDEAL_MAX_PRICE = profile.ideal_max_price
    MAX_PRICE = profile.max_price
    MIN_BEDS = profile.min_beds
    MAX_BEDS = profile.max_beds
    NUM_PEOPLE = profile.num_people
    MOVE_BY = profile.move_by
    NEIGHBORHOODS = list(profile.neighborhoods)
    PREFERRED_NEIGHBORHOODS = list(profile.preferred_neighborhoods)
    FALLBACK_NEIGHBORHOODS = list(profile.fallback_neighborhoods)
    _CL_LOCATION_TO_HOOD = profile.cl_location_to_hood
    REQUIRE_NEIGHBORHOOD_MATCH = profile.require_neighborhood_match
    TARGET_ZIPS = profile.target_zips
    CITY_ZIP_PREFIXES = profile.city_zip_prefixes
    REGION_ZIP_RE = re.compile(profile.region_zip_pattern)
    CRAIGSLIST_BASE = profile.craigslist_base
    DIRECT_SOURCE_SEEDS = list(profile.direct_source_seeds)
    FIRECRAWL_SEEDS = list(profile.firecrawl_seeds)
    PROPERTY_MANAGER_DOMAINS = list(profile.property_manager_domains)
    ZILLOW_RENTAL_URL = profile.zillow_rental_url
    ZILLOW_SEARCH_TERM = profile.zillow_search_term
    ZILLOW_MAP_BOUNDS = profile.zillow_map_bounds
    EXA_NEIGHBORHOODS = list(profile.exa_neighborhoods)
    REDDIT_SUBREDDITS = list(profile.reddit_subreddits)
    LISTING_NOUN = profile.listing_noun
    MIN_BATHROOMS = profile.min_bathrooms
    ENRICH_DETAILS = profile.enrich_details
    CRAIGSLIST_EXTRA_PARAMS = profile.craigslist_extra_params
    PREFERRED_KEYWORDS = profile.preferred_keywords
    if profile.blocked_location_markers:
        pattern = r"\b(" + "|".join(
            m.replace(" ", r"\s+") for m in profile.blocked_location_markers
        ) + r")\b"
        BLOCKED_LOCATION_RE = re.compile(pattern, re.IGNORECASE)
    else:
        BLOCKED_LOCATION_RE = None
    SEEN_PATH = ROOT / f"seen_{profile.key}.json"
    DIGEST_PATH = ROOT / f"digest_{profile.key}_latest.md"
    ARCHIVE_DIR = ROOT / "digests" / profile.key


# Apply SF by default so importers and ad-hoc use have a populated config.
apply_profile(profiles.SF)


# ---------- data ----------

@dataclass
class Listing:
    source: str            # "craigslist", "exa", "exa-reddit"
    id: str                # stable dedup key
    title: str
    url: str
    price: int | None = None
    beds: int | None = None
    neighborhood: str | None = None
    posted: str | None = None    # ISO date string if known
    snippet: str = ""
    query_neighborhood: str | None = None  # the hood we searched for, even if title omits it

    # ---- enrichment fields (populated by enrich_listing from the detail page) ----
    bathrooms: float | None = None
    sqft: int | None = None
    has_office: bool | None = None
    # "confirmed"  -> the detail page explicitly shows a garage/covered parking
    # "none found" -> we read the page and it does NOT mention one (treat as: no garage)
    # None         -> not enriched / page couldn't be read (unverified)
    garage_status: str | None = None
    enriched: bool = False

    def feature_matches(self) -> list[str]:
        """Which PREFERRED_KEYWORDS this listing's text mentions. Soft signal:
        used to rank and badge listings, never to drop them.

        Matches on word boundaries, not bare substrings — otherwise short
        variants like "den" match inside "Denver", "house" inside "townhouse".
        """
        if not PREFERRED_KEYWORDS:
            return []
        hay = f"{self.title} {self.snippet}".lower()
        out = []
        for label, variants in PREFERRED_KEYWORDS:
            if any(re.search(rf"\b{re.escape(v)}\b", hay) for v in variants):
                out.append(label)
        return out

    def matches_neighborhood(self) -> str | None:
        # Craigslist: only trust the seller's location tag, and only if it's an
        # exact match to a known target label. The CL location field is
        # structured enough that substring matching just lets in noise.
        if self.source == "craigslist":
            if self.neighborhood:
                return _CL_LOCATION_TO_HOOD.get(self.neighborhood.strip().lower())
            return None
        # Exa: title/snippet may mention neighborhood loosely, plus we have ZIP fallback.
        haystack = f"{self.title} {self.neighborhood or ''} {self.snippet}".lower()
        for n in NEIGHBORHOODS:
            if n in haystack:
                return n
        return _zip_neighborhood(f"{self.title} {self.snippet}")

    def is_preferred(self) -> bool:
        match = self.matches_neighborhood()
        return match in PREFERRED_NEIGHBORHOODS if match else False

    def domain(self) -> str:
        m = re.search(r"https?://(?:www\.)?([^/]+)", self.url)
        return m.group(1) if m else "?"


@dataclass
class SourceReport:
    source: str
    url: str
    status: str
    listings: int = 0
    note: str = ""


# ---------- seen-set ----------

def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text()))
    except json.JSONDecodeError:
        return set()


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=2))


# ---------- craigslist ----------

_CL_LISTING_RE = re.compile(
    r'<li class="cl-static-search-result"[^>]*>\s*'
    r'<a href="(?P<url>[^"]+)">\s*'
    r'<div class="title">(?P<title>[^<]+)</div>\s*'
    r'<div class="details">\s*'
    r'<div class="price">(?P<price>[^<]*)</div>\s*'
    r'<div class="location">\s*(?P<location>[^<]*?)\s*</div>',
    re.DOTALL,
)


def fetch_craigslist() -> list[Listing]:
    """Pull listings from Craigslist's static (no-JS) HTML search results.

    Note: Craigslist's RSS feed is hard-blocked and their JS-rendered page is
    expensive to crawl. The static search results (rendered for non-JS clients)
    are returned in the HTML and contain title/url/price/location.
    """
    params = {
        "min_price": MIN_PRICE,
        "max_price": MAX_PRICE,
        "min_bedrooms": MIN_BEDS,
        "max_bedrooms": MAX_BEDS,
        "availabilityMode": 0,
    }
    # Profile-specific server-side filters (e.g. housing_type=6 for houses).
    params.update(dict(CRAIGSLIST_EXTRA_PARAMS))
    url = f"{CRAIGSLIST_BASE}?{urlencode(params)}"
    resp = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=30,
    )
    resp.raise_for_status()
    html_text = resp.text

    out: list[Listing] = []
    for m in _CL_LISTING_RE.finditer(html_text):
        link = m.group("url").strip()
        title = html.unescape(m.group("title").strip())
        price_text = m.group("price").strip()
        location = html.unescape(m.group("location").strip())

        pid_match = re.search(r"/(\d{10,})\.html", link)
        listing_id = (
            f"cl-{pid_match.group(1)}"
            if pid_match
            else f"cl-{hashlib.sha1(link.encode()).hexdigest()[:12]}"
        )

        out.append(Listing(
            source="craigslist",
            id=listing_id,
            title=title,
            url=link,
            price=_parse_price(price_text) or _parse_price(title),
            beds=_parse_beds(title),  # often in title, e.g. "2br"
            neighborhood=location or None,
            posted=None,  # not present in static results; leave blank
            snippet="",
        ))
    return out


_PRICE_RE = re.compile(r"\$\s?([\d,]+)")
_BEDS_PATTERNS = [
    re.compile(r"\b(\d)\s*br\b", re.IGNORECASE),
    re.compile(r"\b(\d)\s*bd\b", re.IGNORECASE),
    re.compile(r"\b(\d)\s*bed(?:room)?s?\b", re.IGNORECASE),
]
_BED_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_BED_WORD_RE = re.compile(
    r"\b(one|two|three|four|five)\s+bed(?:room)?s?\b",
    re.IGNORECASE,
)


def _parse_price(title: str) -> int | None:
    m = _PRICE_RE.search(title)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_beds(title: str) -> int | None:
    for pattern in _BEDS_PATTERNS:
        m = pattern.search(title)
        if m:
            return int(m.group(1))
    m = _BED_WORD_RE.search(title)
    if m:
        return _BED_WORDS[m.group(1).lower()]
    return None


# ---------- Reddit owner-direct (reddit-cli) ----------
#
# The owner-direct / by-owner / sublet channel that Exa was supposed to cover but
# can't (Exa is IP-banned, 403s on every call). reddit-cli is the working
# transport: an authenticated, read-only local mirror of Reddit threads
# (see tools/reddit-cli). We sweep a city's classifieds-ish subreddits for posts
# that read like rental OFFERS, then let the same ring/price/bed filters
# downstream decide what survives. These listings can't be detail-page enriched
# (Firecrawl won't scrape Reddit), so they stay "garage unverified" — they're
# leads to DM, not portal listings.

# Marks a post as a REQUEST or discussion ("[ISO] 3br house", "best way to find…"),
# which we never want — we only keep actual offers.
_REDDIT_WANTED_RE = re.compile(
    r"\b(iso\b|in search of|looking (?:for|to)|want(?:ing|ed)? to rent|"
    r"seeking|need(?:ed)? (?:a )?(?:place|house|home|rental|room)|"
    r"help (?:me )?find|anyone (?:have|know|renting)|best way to|"
    r"recommend|advice|how (?:do|to|can)|is it (?:legal|normal|possible))\b",
    re.IGNORECASE,
)
# Marks a post as an OFFER (someone renting a place out).
_REDDIT_OFFER_RE = re.compile(
    r"(for rent\b|renting out|renting my|now leasing|lease takeover|"
    r"available (?:now|\w+ \d)|sublet(?:ting)?\b|sublease|\$[\d,]{3,})",
    re.IGNORECASE,
)
# A post must mention an actual dwelling, not "renting a camera/dress/trailer".
_REDDIT_HOUSING_RE = re.compile(
    r"\b(house|home|apartment|apt\b|condo|townh(?:ome|ouse)|duplex|"
    r"bed(?:room)?s?\b|\dbr\b|\dbd\b|unit|sq ?ft|square feet|lease|sublet)\b",
    re.IGNORECASE,
)
# Drop anything older than this — a months-old Reddit post is a dead listing.
_REDDIT_MAX_AGE_DAYS = 45


def _reddit_cli_path() -> Path:
    """Absolute path to the shared reddit-cli (tools/reddit-cli/reddit-cli)."""
    return ROOT.parent.parent / "tools" / "reddit-cli" / "reddit-cli"


def fetch_reddit() -> tuple[list[Listing], list[SourceReport]]:
    """Owner-direct rental leads from Reddit via the local reddit-cli.

    Best-effort and non-fatal: a missing CLI, an expired session cookie, or a
    failed sub just yields a 'skipped'/'error' coverage row and no listings.
    """
    reports: list[SourceReport] = []
    subs = REDDIT_SUBREDDITS
    if not subs:
        return [], [SourceReport(
            source="reddit", url="", status="skipped",
            note="no reddit_subreddits configured for this city",
        )]

    cli = _reddit_cli_path()
    if not cli.exists():
        return [], [SourceReport(
            source="reddit", url=str(cli), status="skipped",
            note="reddit-cli not found",
        )]

    query = (
        f'"for rent" OR rental OR renting OR landlord OR sublet '
        f'OR "{LISTING_NOUN} for rent"'
    )

    # 1) Refresh the local mirror for each sub (best-effort; ignore failures here,
    #    the export step below reports per-sub status).
    for sub in subs:
        try:
            subprocess.run(
                [sys.executable, str(cli), "sync", "-r", sub,
                 "-q", query, "--limit", "30", "--agent"],
                capture_output=True, text=True, timeout=150,
            )
        except (subprocess.SubprocessError, OSError):
            continue

    # 2) Export each sub's mirror and keep fresh OFFER posts.
    out: list[Listing] = []
    cutoff = time.time() - _REDDIT_MAX_AGE_DAYS * 86400
    for sub in subs:
        sub_url = f"https://www.reddit.com/r/{sub}/"
        try:
            proc = subprocess.run(
                [sys.executable, str(cli), "export", "-r", sub, "--json"],
                capture_output=True, text=True, timeout=90,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            reports.append(SourceReport(source=f"reddit:{sub}", url=sub_url,
                                        status="error", note=str(exc)[:80]))
            continue
        if proc.returncode != 0:
            reports.append(SourceReport(source=f"reddit:{sub}", url=sub_url,
                                        status="error",
                                        note=(proc.stderr or "export failed").strip()[:80]))
            continue
        try:
            payload = json.loads(proc.stdout or "null")
        except json.JSONDecodeError:
            reports.append(SourceReport(source=f"reddit:{sub}", url=sub_url,
                                        status="error", note="unparseable json"))
            continue

        posts = payload.get("posts", []) if isinstance(payload, dict) else (payload or [])
        kept = 0
        for p in posts:
            title = (p.get("title") or "").strip()
            body = (p.get("selftext") or "").strip()
            text = f"{title}\n{body}"
            created = p.get("created_utc")
            if created and float(created) < cutoff:
                continue
            # A title ending in "?" is almost always a question, not a listing.
            if title.endswith("?"):
                continue
            if _REDDIT_WANTED_RE.search(text):
                continue
            if not _REDDIT_OFFER_RE.search(text):
                continue
            if not _REDDIT_HOUSING_RE.search(text):
                continue
            # A link-post pointing off-site (e.g. a news article about rent
            # prices) is never an owner's listing — those are always self/text
            # posts. Drop anything whose link leaves reddit.com.
            ext = p.get("url") or ""
            if ext and "reddit.com" not in ext:
                continue

            permalink = p.get("permalink") or ""
            # Always link to the reddit thread itself (so the /r/ gate passes and
            # the card opens the post, not an off-site mirror).
            url = (f"https://www.reddit.com{permalink}" if permalink else ext)
            if not url or "/r/" not in url:
                continue
            pid = p.get("id") or hashlib.sha1(url.encode()).hexdigest()[:12]
            posted = None
            if created:
                posted = dt.datetime.utcfromtimestamp(float(created)).date().isoformat()

            out.append(Listing(
                source=f"reddit:{sub}",
                id=f"reddit-{pid}",
                title=title or "(reddit post)",
                url=url,
                price=_parse_price(title) or _parse_price(body),
                beds=_parse_beds(title) or _parse_beds(body),
                neighborhood=None,  # let matches_neighborhood scan title+snippet
                posted=posted,
                snippet=body[:500],
            ))
            kept += 1

        reports.append(SourceReport(source=f"reddit:{sub}", url=sub_url,
                                    status="ok", listings=kept,
                                    note=f"{kept} offer post(s)"))
    return out, reports


# ---------- direct public sources ----------

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_HREF_RE = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_DIRECT_URL_RE = re.compile(
    r"(#|mailto:|tel:|javascript:|/privacy|/terms|/login|/sign[-_]?in|"
    r"/contact|/about|/blog|/careers|/press|/help|/sell|"
    r"\.(?:jpg|jpeg|png|gif|svg|webp|pdf|css|js)(?:\?|$))",
    re.IGNORECASE,
)


def fetch_direct_public_sources() -> tuple[list[Listing], list[SourceReport]]:
    """Fetch public search/vacancy pages and parse listing-looking records.

    This avoids logins, CAPTCHA handling, browser automation, and private
    sessions. Sites that return a block page or JS-only shell are recorded in
    the coverage report and still get Exa fallback coverage later.
    """
    listings: list[Listing] = []
    reports: list[SourceReport] = []

    for domain, url in DIRECT_SOURCE_SEEDS:
        source = f"direct:{domain}"
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            reports.append(SourceReport(source=source, url=url, status="error", note=str(exc)[:140]))
            continue

        if resp.status_code in {401, 403, 429}:
            reports.append(SourceReport(
                source=source,
                url=url,
                status="blocked",
                note=f"HTTP {resp.status_code}; kept Exa fallback only",
            ))
            time.sleep(0.2)
            continue
        if resp.status_code >= 400:
            reports.append(SourceReport(
                source=source,
                url=url,
                status="error",
                note=f"HTTP {resp.status_code}",
            ))
            time.sleep(0.2)
            continue

        html_text = resp.text
        parsed = _parse_direct_page(source, url, html_text)
        listings.extend(parsed)
        reports.append(SourceReport(
            source=source,
            url=url,
            status="ok",
            listings=len(parsed),
            note="parsed public page",
        ))
        time.sleep(0.2)

    return listings, reports


def _parse_direct_page(source: str, base_url: str, html_text: str) -> list[Listing]:
    out: list[Listing] = []
    seen_urls: set[str] = set()

    for ls in _parse_jsonld_listings(source, base_url, html_text):
        if ls.url not in seen_urls:
            seen_urls.add(ls.url)
            out.append(ls)

    for ls in _parse_anchor_listings(source, base_url, html_text):
        if ls.url not in seen_urls:
            seen_urls.add(ls.url)
            out.append(ls)

    return out


def _parse_jsonld_listings(source: str, base_url: str, html_text: str) -> list[Listing]:
    out: list[Listing] = []
    for m in _JSONLD_RE.finditer(html_text):
        raw = html.unescape(m.group(1).strip())
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for node in _iter_json_nodes(data):
            if not isinstance(node, dict):
                continue
            url = _node_url(node)
            title = _node_text_value(node, "name") or _node_text_value(node, "headline")
            description = _node_text_value(node, "description")
            if not url or not (title or description):
                continue
            full_url = urljoin(base_url, url)
            text = _clean_text(" ".join(filter(None, [
                title,
                description,
                _node_text_value(node, "address"),
                _node_text_value(node, "offers"),
            ])))
            if not _RENTAL_HINTS.search(text):
                continue

            out.append(Listing(
                source=source,
                id=_url_listing_id(source, full_url),
                title=title or full_url,
                url=full_url,
                price=_price_from_jsonld(node) or _parse_price(text),
                beds=_parse_beds(text),
                neighborhood=None,
                posted=_node_text_value(node, "datePosted"),
                snippet=text[:500],
            ))
    return out


def _parse_anchor_listings(source: str, base_url: str, html_text: str) -> list[Listing]:
    out: list[Listing] = []
    for m in _HREF_RE.finditer(html_text):
        href = m.group(1).strip()
        if _SKIP_DIRECT_URL_RE.search(href):
            continue

        full_url = urljoin(base_url, html.unescape(href))
        parsed = urlparse(full_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc and urlparse(base_url).netloc.replace("www.", "") not in parsed.netloc.replace("www.", ""):
            continue

        window_start = max(0, m.start() - 900)
        window_end = min(len(html_text), m.end() + 900)
        context = _clean_text(html_text[window_start:window_end])
        anchor_text = _clean_text(m.group(2))
        title = anchor_text or context[:140] or full_url
        haystack = f"{title} {context}"

        if not _RENTAL_HINTS.search(haystack):
            continue
        if _is_aggregator_url(full_url) and not _PRICE_RE.search(haystack):
            continue

        out.append(Listing(
            source=source,
            id=_url_listing_id(source, full_url),
            title=title[:180],
            url=full_url,
            price=_parse_price(haystack),
            beds=_parse_beds(haystack),
            neighborhood=None,
            posted=None,
            snippet=context[:500],
        ))
    return out


def _iter_json_nodes(data: object) -> Iterable[object]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_json_nodes(item)
        return
    if not isinstance(data, dict):
        return

    yield data
    for key in ("@graph", "itemListElement", "mainEntity", "about", "offers"):
        value = data.get(key)
        if value is not None:
            yield from _iter_json_nodes(value)
    item = data.get("item")
    if isinstance(item, dict):
        yield from _iter_json_nodes(item)


def _node_url(node: dict) -> str | None:
    for key in ("url", "@id"):
        value = node.get(key)
        if isinstance(value, str):
            return value
    item = node.get("item")
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return _node_url(item)
    return None


def _node_text_value(node: object, key: str) -> str | None:
    if not isinstance(node, dict):
        return None
    value = node.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_node_text_value(item, key) or _flatten_json_text(item) for item in value]
        return " ".join(part for part in parts if part)
    if isinstance(value, dict):
        return _flatten_json_text(value)
    return None


def _flatten_json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_flatten_json_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_flatten_json_text(v) for v in value.values())
    return ""


def _price_from_jsonld(node: dict) -> int | None:
    offers = node.get("offers")
    if isinstance(offers, dict):
        for key in ("price", "lowPrice", "highPrice"):
            value = offers.get(key)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                parsed = _parse_price(f"${value}") or _parse_price(value)
                if parsed:
                    return parsed
    return None


def _clean_text(raw: str) -> str:
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", raw)
    without_tags = _TAG_RE.sub(" ", without_scripts)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _url_listing_id(source: str, url: str) -> str:
    return f"{source}-{hashlib.sha1(_normalized_url(url).encode()).hexdigest()[:14]}"


def _normalized_url(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", ""))


# ---------- exa ----------

def _bed_phrase() -> str:
    if MIN_BEDS == MAX_BEDS:
        return f"{MIN_BEDS} bedroom"
    return f"{MIN_BEDS} to {MAX_BEDS} bedroom"


def _exa_target_phrase() -> str:
    """The neighborhoods to name in the per-aggregator query, falling back to
    the city itself for a whole-metro search."""
    hoods = list(PREFERRED_NEIGHBORHOODS) or list(EXA_NEIGHBORHOODS) or list(NEIGHBORHOODS)
    if hoods:
        named = ", ".join(h.title() for h in hoods[:5])
        return f"{named}, {CITY_LABEL}"
    return f"{CITY_LABEL}, {STATE_LABEL}"


# Circuit breaker. Exa is rate-limited, not dead: it serves ~100 listings then
# starts 403ing partway through the ~60-query sweep. Two failure modes to handle:
#   1) a transient 403/429 on one query → short retry, then move on.
#   2) a sustained block (every query 403s) → without a breaker that's
#      60 queries × retries = many minutes of dead waiting.
# So we retry each query briefly, and only disable Exa for the rest of the run
# after _EXA_FAIL_LIMIT *consecutive* exhausted failures (a real sustained block,
# not the normal mid-sweep rate-limit that still let earlier queries through).
_EXA_DISABLED = False
_EXA_CONSEC_FAILS = 0
_EXA_FAIL_LIMIT = 5


def fetch_exa(api_key: str) -> list[Listing]:
    """Run a per-neighborhood neural sweep + a Reddit-specific sweep.

    For a whole-metro profile with no curated EXA_NEIGHBORHOODS, the per-hood
    passes collapse to a single city-wide query so we don't burn credits looping
    dozens of neighborhoods.

    If Exa returns 403 (the current IP ban), the circuit breaker trips on the
    first query and the rest of the sweep is skipped — the run falls straight
    through to Firecrawl/Craigslist/Reddit instead of stalling for 30+ minutes.
    """
    global _EXA_DISABLED, _EXA_CONSEC_FAILS
    _EXA_DISABLED = False  # fresh start each run
    _EXA_CONSEC_FAILS = 0
    start_pub = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    bed = _bed_phrase()
    default_hood = PREFERRED_NEIGHBORHOODS[0] if PREFERRED_NEIGHBORHOODS else None
    out: list[Listing] = []

    # Soft feature bias for the open-web pass (e.g. "with a garage, office, open
    # floor plan"). Built from the profile's preferred_keywords labels.
    feature_labels = [label for label, _ in PREFERRED_KEYWORDS if label != LISTING_NOUN]
    feature_phrase = f" with a {', '.join(feature_labels)}" if feature_labels else ""

    sweep_hoods = list(EXA_NEIGHBORHOODS) or [None]

    # Pass 1: open-web neural search, per neighborhood (or city-wide).
    for n in sweep_hoods:
        where = f"{n.title()}, {CITY_LABEL}" if n else f"{CITY_LABEL}, {STATE_LABEL}"
        q = (
            f"{bed} {LISTING_NOUN} for rent in {where} available now{feature_phrase}, "
            f"with monthly rent price and address"
        )
        out.extend(_exa_search(api_key, q, start_pub, n, source="exa", num_results=10))

    # Pass 2: Reddit sweep — people post sublets and rental leads on the local
    # subreddit that aggregators never see. Restrict to user-thread URLs in keep().
    for n in sweep_hoods:
        where = f"{n.title()}, {CITY_LABEL}" if n else f"{CITY_LABEL}, {STATE_LABEL}"
        q = (
            f"Reddit post: looking to sublet or rent out a {bed} {LISTING_NOUN} "
            f"in {where}, with rent and details"
        )
        out.extend(_exa_search(
            api_key, q, start_pub, n, source="exa-reddit",
            include_domains=REDDIT_DOMAINS, num_results=6,
        ))

    # Pass 3: per-aggregator sweep. Many sites gate their search pages, so we ask
    # Exa to return deep listing URLs from each domain. _is_aggregator_url drops
    # hub/category pages.
    target_phrase = _exa_target_phrase()
    for domain in AGGREGATOR_DOMAINS:
        q = (
            f"{bed} {LISTING_NOUN} for rent in {target_phrase} "
            f"with monthly rent price and address, available now"
        )
        out.extend(_exa_search(
            api_key, q, start_pub, default_hood, source="exa-aggregator",
            include_domains=[domain], num_results=10,
        ))

    # Pass 4: local property manager sweep. These often list classic buildings
    # before or instead of aggregator feeds.
    for domain in PROPERTY_MANAGER_DOMAINS:
        q = (
            f"{bed} {LISTING_NOUN} for rent in {target_phrase}, "
            f"with monthly rent price and address"
        )
        out.extend(_exa_search(
            api_key, q, start_pub, default_hood, source="exa-manager",
            include_domains=[domain], num_results=5,
        ))

    return out


def _exa_search(
    api_key: str,
    query: str,
    start_pub: str,
    query_hood: str,
    source: str,
    include_domains: list[str] | None = None,
    num_results: int = 8,
) -> list[Listing]:
    body: dict = {
        "query": query,
        "numResults": num_results,
        "useAutoprompt": True,
        "type": "neural",
        "startPublishedDate": start_pub,
        "contents": {
            "text": {"maxCharacters": 800},
            "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
        },
    }
    if include_domains:
        body["includeDomains"] = include_domains

    # Circuit breaker already tripped this run (Exa sustained-blocked) — skip.
    global _EXA_DISABLED, _EXA_CONSEC_FAILS
    if _EXA_DISABLED:
        return []

    # Rate-limit (403/429): retry a couple of times with a short backoff, then
    # give up on THIS query and move on. Capped low so a mid-sweep limit costs
    # seconds, not minutes. A successful query resets the consecutive-fail count;
    # _EXA_FAIL_LIMIT consecutive exhausted failures disables Exa for the run.
    resp = None
    attempts = 3
    for attempt in range(attempts):
        try:
            resp = requests.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key, "content-type": "application/json"},
                json=body,
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"  exa query failed ({query!r}): {exc}", file=sys.stderr)
            resp = None
            break
        if resp.status_code in (403, 429) and attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))  # 1.5, 3.0s — ride out a brief limit
            continue
        break

    failed = resp is None or resp.status_code != 200
    if failed:
        _EXA_CONSEC_FAILS += 1
        if resp is not None:
            print(f"  exa query failed ({query!r}): HTTP {resp.status_code}", file=sys.stderr)
        if _EXA_CONSEC_FAILS >= _EXA_FAIL_LIMIT:
            _EXA_DISABLED = True
            print(f"  exa disabled for this run after {_EXA_CONSEC_FAILS} consecutive "
                  "failures (sustained rate-limit/block). Skipping remaining queries.",
                  file=sys.stderr)
        return []

    _EXA_CONSEC_FAILS = 0  # this query worked; reset the streak

    out: list[Listing] = []
    for r in resp.json().get("results", []):
        url = r.get("url", "")
        if not url or _is_aggregator_url(url):
            continue
        listing_id = f"{source}-{hashlib.sha1(url.encode()).hexdigest()[:14]}"

        title = html.unescape((r.get("title") or url).strip())
        text = (r.get("text") or "").strip()
        highlights = " ".join(r.get("highlights") or [])
        snippet = (highlights or text)[:500]
        snippet = re.sub(r"\s+", " ", snippet)

        out.append(Listing(
            source=source,
            id=listing_id,
            title=title,
            url=url,
            price=_parse_price(f"{title} {text}"),
            beds=_parse_beds(f"{title} {text}"),
            neighborhood=None,
            posted=r.get("publishedDate"),
            snippet=snippet,
            query_neighborhood=query_hood,
        ))
    time.sleep(0.15)
    return out


# Patterns that indicate Exa returned a category/index page, not a real listing.
_AGGREGATOR_PATTERNS = re.compile(
    r"/(\d+-bedroom-apartments-for-rent|apartments-for-rent|"
    r"apartments_for_rent|houses-for-rent|condos-for-rent|"
    r"rentals?/?$|search/?|browse/?|category/|tag/|topics?/|floorplans?/?)",
    re.IGNORECASE,
)

# URL paths that mean "for sale" rather than rental.
_FOR_SALE_PATTERNS = re.compile(
    r"/(homes?-for-sale|for-sale|homes-for-sale-details|sales?/|listings?/sale)",
    re.IGNORECASE,
)

# Titles like "Marina District Apartments" without a specific address are hub pages.
_HUB_TITLE_PATTERNS = re.compile(
    r"(^\s*\d+\s+(apartments|rentals|homes)\s+for\s+rent|"
    r"apartments\s+for\s+rent\s+\|\s+san\s+francisco|"
    r"apartments\s+for\s+rent\s+in\s+san\s+francisco)",
    re.IGNORECASE,
)

# House-mode hard filter: when LISTING_NOUN == "house", drop anything that reads
# as an apartment/condo complex or a "N Bedroom Apartments for Rent" hub page —
# unless it ALSO carries a house marker (rescues real house pages that mention
# the word "apartment" incidentally).
_NON_HOUSE_RE = re.compile(
    r"(\bapartments?\b|\bapt\b|\bcondos?\b|\bcondominiums?\b|"
    r"\bunits?\s+(?:available|for\s+rent)|"
    r"[- ](?:one|two|three|four|\d)[- ]bedroom\s+apartments?\b|"
    r"\bapartment\s+(?:community|complex|homes?))",
    re.IGNORECASE,
)
_HOUSE_RE = re.compile(
    r"(\bhouse\b|\bsingle[\s-]family\b|\bdetached\b|\bsfh\b|\bbungalow\b)",
    re.IGNORECASE,
)
# Zillow building pages (/apartments/<complex>, /b/<building>) are apartment
# complexes even when titled with a bare street address, so the text patterns
# above never see the word "apartment". The URL is the only reliable signal.
_NON_HOUSE_URL_RE = re.compile(r"zillow\.com/(apartments|b)/", re.IGNORECASE)

# Sale-style prices: 6+ digit dollar amounts ($500,000+). Real SF rents top out
# in the low tens of thousands per month even at the high end.
_SALE_PRICE_RE = re.compile(r"\$\s?[1-9]\d{0,2},\d{3},\d{3}|\$\s?[1-9]\d{2},\d{3}")


def _is_aggregator_url(url: str) -> bool:
    """True for category/index/search pages that aren't a single listing."""
    if _AGGREGATOR_PATTERNS.search(url):
        return True
    if _FOR_SALE_PATTERNS.search(url):
        return True
    # Highrises.com / similar: /apartments/{Neighborhood-Name_City_State} = hub
    if re.search(r"/apartments/[A-Z][a-z]+-?\w*_[A-Z]", url):
        return True
    if "zillow.com" in url and "/homedetails/" not in url and "/b/" not in url:
        return True
    if "apartments.com" in url and url.rstrip("/").count("/") < 4:
        return True
    return False


# ---------- firecrawl-routed sources ----------
#
# Sites that either anti-bot block our direct fetch (403/429) or serve a
# JS-only React shell with no inventory in the raw HTML. We route them
# through Firecrawl's /v1/scrape with structured JSON extraction (LLM-based)
# instead of writing a custom parser per site.
#
# Cost per call: roughly 5-10 Firecrawl credits per site for extraction +
# JS rendering. 21 sites/day ≈ 150 credits/day ≈ 4,500/month.
#
# The per-city seed URLs live on each SearchProfile (profiles.py) and are bound
# into the FIRECRAWL_SEEDS global by apply_profile(). The Firecrawl schema below
# tells the LLM what fields to pull, so the same extraction logic works across
# every site layout without site-specific code.

# Firecrawl LLM extraction schema. Same shape every site is normalized into.
_FIRECRAWL_LISTING_SCHEMA = {
    "type": "object",
    "properties": {
        "listings": {
            "type": "array",
            "description": (
                "Rental apartment listings shown on this page. Include only items "
                "that look like real for-rent units with a monthly price; skip "
                "ads, neighborhood hub pages, navigation links, and for-sale items."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Listing title or property name"},
                    "url": {"type": "string", "description": "Detail-page URL (absolute or relative)"},
                    "price_per_month": {
                        "type": "number",
                        "description": "Monthly rent in USD as a number (no $ or commas)",
                    },
                    "bedrooms": {"type": "number", "description": "Bedroom count"},
                    "address": {"type": "string", "description": "Street address if shown"},
                    "neighborhood": {"type": "string", "description": "Neighborhood name if shown"},
                },
                "required": ["title", "url"],
            },
        },
    },
    "required": ["listings"],
}


def fetch_firecrawl_sources(api_key: str) -> tuple[list[Listing], list[SourceReport]]:
    """Pull rentals from JS-only / anti-bot-blocked sites via Firecrawl structured extract."""
    listings: list[Listing] = []
    reports: list[SourceReport] = []

    for domain, url in FIRECRAWL_SEEDS:
        source = f"firecrawl:{domain}"
        try:
            resp = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["json"],
                    "jsonOptions": {"schema": _FIRECRAWL_LISTING_SCHEMA},
                    "waitFor": 5000,
                    "timeout": 90000,   # Firecrawl-side timeout (ms); some aggregators are slow
                },
                timeout=180,
            )
        except requests.RequestException as exc:
            reports.append(SourceReport(source=source, url=url, status="error", note=str(exc)[:140]))
            continue

        if resp.status_code != 200:
            reports.append(SourceReport(
                source=source, url=url, status="error",
                note=f"firecrawl HTTP {resp.status_code}: {resp.text[:120]}",
            ))
            continue

        payload = resp.json()
        if not payload.get("success"):
            reports.append(SourceReport(
                source=source, url=url, status="error",
                note=f"firecrawl error: {str(payload.get('error', ''))[:140]}",
            ))
            continue

        data = (payload.get("data") or {}).get("json") or {}
        raw_items = data.get("listings") or []

        parsed: list[Listing] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            detail_url = (item.get("url") or "").strip()
            if not detail_url:
                continue
            if detail_url.startswith("/"):
                detail_url = urljoin(url, detail_url)
            if not detail_url.startswith("http"):
                continue

            title_raw = item.get("title") or item.get("address") or detail_url
            address = (item.get("address") or "").strip()
            hood = (item.get("neighborhood") or "").strip()

            price = item.get("price_per_month")
            if isinstance(price, str):
                price = _parse_price(price)
            price_int = int(price) if isinstance(price, (int, float)) else None
            # LLM uses -1 / 0 as a "no price shown" sentinel — treat as unknown.
            if price_int is not None and price_int <= 0:
                price_int = None

            beds_raw = item.get("bedrooms")
            beds_int = None
            if isinstance(beds_raw, (int, float)):
                beds_int = int(beds_raw)
            elif isinstance(beds_raw, str):
                m = re.search(r"\d+", beds_raw)
                if m:
                    beds_int = int(m.group())

            snippet = " | ".join(p for p in [address, hood] if p)[:500]

            parsed.append(Listing(
                source=source,
                id=_url_listing_id(source, detail_url),
                title=str(title_raw)[:180],
                url=detail_url,
                price=price_int,
                beds=beds_int,
                neighborhood=address or hood or None,
                posted=None,
                snippet=snippet,
            ))

        listings.extend(parsed)
        reports.append(SourceReport(
            source=source, url=url, status="ok", listings=len(parsed),
            note="firecrawl rendered + json schema extracted",
        ))
        time.sleep(0.1)

    return listings, reports


# ---------- zillow (via firecrawl) ----------
#
# Direct-fetching Zillow returns 403 (PerimeterX). Firecrawl handles JS
# rendering + rotating residential proxies, so we route Zillow through their
# /v1/scrape endpoint and parse the __NEXT_DATA__ JSON blob that Zillow embeds
# on every search results page. Costs ~1 Firecrawl credit per URL fetched.
#
# Gated on FIRECRAWL_API_KEY; if missing, we skip with a warning (same way Exa
# would be skipped if EXA_API_KEY were missing, except Exa is currently
# required). Falls back gracefully — Zillow detail pages are still indexed by
# Exa, so a Firecrawl outage doesn't blind the whole pipeline.

# ZILLOW_RENTAL_URL, ZILLOW_SEARCH_TERM, and ZILLOW_MAP_BOUNDS are profile globals
# (see apply_profile). Everything else here is city-agnostic.

_ZILLOW_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


ZILLOW_MAX_PAGES = 3  # Zillow returns ~40 listings/page; 3 pages covers a metro's 3BR inventory


def fetch_zillow_firecrawl(api_key: str) -> tuple[list[Listing], SourceReport]:
    """Pull SF rentals from Zillow via Firecrawl, paginated across ZILLOW_MAX_PAGES."""
    all_listings: list[Listing] = []
    seen_ids: set[str] = set()
    pages_fetched = 0
    errors: list[str] = []

    for page in range(1, ZILLOW_MAX_PAGES + 1):
        page_listings, error = _fetch_zillow_page(api_key, page)
        pages_fetched += 1
        if error:
            errors.append(f"page {page}: {error}")
            # Don't bail on a single page failure — try the next page.
            continue
        new_count = 0
        for ls in page_listings:
            if ls.id in seen_ids:
                continue
            seen_ids.add(ls.id)
            all_listings.append(ls)
            new_count += 1
        # If a page returns nothing new, we're past the end of results — stop.
        if new_count == 0 and page > 1:
            break

    note_bits = [
        f"firecrawl rendered + __NEXT_DATA__ parsed across {pages_fetched} page(s)",
    ]
    if errors:
        note_bits.append(f"errors: {'; '.join(errors)[:200]}")

    status = "ok" if all_listings else ("error" if errors else "ok")
    return all_listings, SourceReport(
        source="zillow",
        url=ZILLOW_RENTAL_URL,
        status=status,
        listings=len(all_listings),
        note=" | ".join(note_bits),
    )


def _fetch_zillow_page(api_key: str, page: int) -> tuple[list[Listing], str | None]:
    """Fetch a single page of Zillow SF rentals. Returns (listings, error_msg)."""
    # Zillow uses an opaque searchQueryState query param to encode filters and
    # pagination. We pre-filter on price/beds server-side so we don't waste
    # credits on listings that would be dropped anyway. mapBounds covers the metro.
    search_state = {
        "pagination": {"currentPage": page} if page > 1 else {},
        "usersSearchTerm": ZILLOW_SEARCH_TERM,
        "mapBounds": dict(ZILLOW_MAP_BOUNDS),
        "isMapVisible": False,
        "filterState": {
            "fr": {"value": True},     # for rent
            "fsba": {"value": False},  # exclude for-sale-by-agent
            "fsbo": {"value": False},  # exclude for-sale-by-owner
            "nc": {"value": False},    # exclude new construction
            "cmsn": {"value": False},
            "auc": {"value": False},
            "fore": {"value": False},
            "mp": {"min": MIN_PRICE, "max": MAX_PRICE},
            "beds": {"min": MIN_BEDS, "max": MAX_BEDS},
        },
        "isListVisible": True,
    }
    if LISTING_NOUN == "house":
        # Exclude apartment/condo/multifamily home types server-side — unset
        # types default to ON, which is how apartment complexes (bare-address
        # titles, /apartments/ URLs) were reaching the digest. Townhomes stay,
        # matching the text filter. Also saves the credits spent enriching them.
        for key in ("apa", "apco", "con", "mf", "manu", "land"):
            search_state["filterState"][key] = {"value": False}
    from urllib.parse import quote  # local import keeps top-of-file diff small
    full_url = f"{ZILLOW_RENTAL_URL}?searchQueryState={quote(json.dumps(search_state))}"

    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": full_url,
                "formats": ["rawHtml"],
                "waitFor": 3000,  # let the React app paint __NEXT_DATA__
            },
            timeout=90,
        )
    except requests.RequestException as exc:
        return [], str(exc)[:140]

    if resp.status_code != 200:
        return [], f"firecrawl HTTP {resp.status_code}: {resp.text[:120]}"

    payload = resp.json()
    if not payload.get("success"):
        return [], f"firecrawl error: {str(payload.get('error', ''))[:140]}"

    html_text = (payload.get("data") or {}).get("rawHtml") or ""
    if not html_text:
        return [], "empty rawHtml"

    return _parse_zillow_html(html_text), None


def _parse_zillow_html(html_text: str) -> list[Listing]:
    """Extract listings from Zillow's __NEXT_DATA__ JSON blob.

    Zillow's structure (as of 2026): props.pageProps.searchPageState.cat1
    .searchResults.listResults — array of dicts with zpid/detailUrl/address/
    price/beds. This schema has shifted before and will shift again; on any
    parse miss we return [] and let the source report show 0 listings so it's
    visible in coverage.
    """
    m = _ZILLOW_NEXT_DATA_RE.search(html_text)
    if not m:
        return []
    try:
        data = json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return []

    cur = data
    for key in ("props", "pageProps", "searchPageState", "cat1", "searchResults", "listResults"):
        if not isinstance(cur, dict):
            return []
        cur = cur.get(key)
        if cur is None:
            return []
    if not isinstance(cur, list):
        return []

    out: list[Listing] = []
    for node in cur:
        if not isinstance(node, dict):
            continue
        detail_url = node.get("detailUrl") or node.get("hdpUrl")
        if not detail_url:
            continue
        if detail_url.startswith("/"):
            detail_url = f"https://www.zillow.com{detail_url}"

        zpid = node.get("zpid") or node.get("id")
        address = node.get("address") or ""
        status_text = node.get("statusText") or ""
        title = address or status_text or detail_url

        price = None
        price_raw = node.get("unformattedPrice") or node.get("price")
        if isinstance(price_raw, (int, float)):
            price = int(price_raw)
        elif isinstance(price_raw, str):
            price = _parse_price(price_raw)

        beds = None
        beds_raw = node.get("beds")
        if isinstance(beds_raw, (int, float)):
            beds = int(beds_raw)

        # Stuff the full address into both `neighborhood` (for the digest
        # display) and the snippet (so matches_neighborhood() can find a SF
        # ZIP or neighborhood string in the haystack).
        snippet_parts = [address, status_text]
        var_data = node.get("variableData")
        if isinstance(var_data, dict):
            vd_text = var_data.get("text")
            if isinstance(vd_text, str):
                snippet_parts.append(vd_text)
        snippet = " | ".join(p for p in snippet_parts if p)[:500]

        listing_id = (
            f"zillow-{zpid}"
            if zpid
            else f"zillow-{hashlib.sha1(detail_url.encode()).hexdigest()[:12]}"
        )

        out.append(Listing(
            source="zillow",
            id=listing_id,
            title=title[:180],
            url=detail_url,
            price=price,
            beds=beds,
            neighborhood=address or None,
            posted=None,
            snippet=snippet,
        ))
    return out


# ---------- detail-page enrichment ----------
#
# The search/aggregator passes give us thin snippets — often just a title and a
# price. That's not enough to know whether a house actually has a garage, a
# second bathroom, or an office. So for profiles with enrich_details=True, we
# fetch each surviving candidate's FULL detail page via Firecrawl and pull a
# structured record. Then "garage not mentioned" reliably means "no garage",
# because we read the whole page, not a 500-char blurb.
#
# Cost: ~1-5 Firecrawl credits per listing. We only enrich candidates that
# already passed keep_basic(), and cap the count, so spend stays bounded.

ENRICH_CAP = 160  # max detail pages to fetch per run (cost ceiling)

_ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "is_single_listing": {
            "type": "string", "enum": ["yes", "no"],
            "description": (
                "'yes' if this page is ONE specific home/house for rent with its "
                "own details; 'no' if it's a search-results, map, or hub page."
            ),
        },
        "bedrooms": {"type": "number", "description": "Number of bedrooms"},
        "bathrooms": {"type": "number", "description": "Number of bathrooms (e.g. 2, 2.5)"},
        "monthly_rent": {"type": "number", "description": "Monthly rent in USD, digits only"},
        "has_garage": {
            "type": "string", "enum": ["yes", "no"],
            "description": (
                "Answer 'yes' ONLY if the listing explicitly indicates a garage "
                "or a dedicated attached/covered parking space (e.g. '2-car "
                "garage', 'attached garage', 'carport'). If a garage is not "
                "clearly mentioned anywhere on the page, answer 'no'. Do not guess."
            ),
        },
        "parking_description": {
            "type": "string",
            "description": "The exact parking text from the page, if any (e.g. '2-car attached garage').",
        },
        "has_office": {
            "type": "string", "enum": ["yes", "no"],
            "description": "'yes' if the home has an office, den, study, or bonus/flex room.",
        },
        "square_feet": {"type": "number", "description": "Interior square footage"},
        "address": {"type": "string", "description": "Full street address including ZIP, if shown"},
        "is_available": {
            "type": "string", "enum": ["yes", "no", "unknown"],
            "description": "'no' if the page says rented/leased/no longer available.",
        },
    },
    "required": ["has_garage"],
}


def enrich_listing(api_key: str, listing: Listing) -> None:
    """Fetch a listing's detail page and fill garage/bath/bed/office/sqft in place.

    On any failure the listing is left as-is (enriched stays False, garage_status
    stays None = 'unverified'), so a Firecrawl hiccup never drops a candidate.
    """
    # Reddit threads can't be scraped by Firecrawl — don't waste a credit; leave
    # them unverified (they're DM-the-owner leads, not portal detail pages).
    if listing.source.startswith("reddit"):
        return
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": listing.url,
                "formats": ["json"],
                "jsonOptions": {"schema": _ENRICH_SCHEMA},
                "waitFor": 3500,
                "timeout": 60000,
            },
            timeout=90,
        )
    except requests.RequestException:
        return
    if resp.status_code != 200:
        return
    payload = resp.json()
    if not payload.get("success"):
        return
    data = (payload.get("data") or {}).get("json") or {}
    if not isinstance(data, dict):
        return

    # If Firecrawl decided this is a hub/search page, don't trust the fields —
    # leave the listing unverified rather than stamping a bogus garage answer.
    if str(data.get("is_single_listing", "")).lower() == "no":
        return

    listing.enriched = True

    garage = str(data.get("has_garage", "")).lower()
    if garage == "yes":
        listing.garage_status = "confirmed"
    elif garage == "no":
        listing.garage_status = "none found"

    listing.has_office = str(data.get("has_office", "")).lower() == "yes"

    baths = data.get("bathrooms")
    if isinstance(baths, (int, float)) and baths > 0:
        listing.bathrooms = float(baths)

    beds = data.get("bedrooms")
    if isinstance(beds, (int, float)) and beds > 0:
        listing.beds = int(beds)

    sqft = data.get("square_feet")
    if isinstance(sqft, (int, float)) and sqft > 0:
        listing.sqft = int(sqft)

    rent = data.get("monthly_rent")
    if listing.price is None and isinstance(rent, (int, float)) and rent > 0:
        listing.price = int(rent)

    # The full address is the most reliable neighborhood/ZIP signal — fold it
    # into the fields the ring filter reads so it can place the house correctly.
    address = (data.get("address") or "").strip()
    if address:
        listing.neighborhood = address
        listing.snippet = (f"{address} | {listing.snippet}")[:500]

    # An explicit "no longer available" → let the stale filter catch it.
    if str(data.get("is_available", "")).lower() == "no":
        listing.snippet = (f"{listing.snippet} no longer available")[:520]


def enrich_candidates(api_key: str, candidates: list[Listing]) -> int:
    """Enrich up to ENRICH_CAP candidates, ring-likely ones first. Returns the
    number enriched. Prints what was skipped so a cap is never silent."""
    ordered = sorted(candidates, key=lambda ls: 0 if ls.matches_neighborhood() else 1)
    targets = ordered[:ENRICH_CAP]
    skipped = len(ordered) - len(targets)
    print(f"Enriching {len(targets)} detail pages via Firecrawl…", flush=True)
    if skipped:
        print(f"  (cost cap: {skipped} candidate(s) not enriched this run)")
    for i, ls in enumerate(targets, 1):
        enrich_listing(api_key, ls)
        if i % 10 == 0:
            print(f"  enriched {i}/{len(targets)}", flush=True)
        time.sleep(0.1)
    return len(targets)


# ---------- filtering ----------


def _zip_in_city(zip_code: str) -> bool:
    return any(zip_code.startswith(p) for p in CITY_ZIP_PREFIXES)


def _zip_neighborhood(text: str) -> str | None:
    """Return the target neighborhood if the text contains a known target ZIP."""
    for m in REGION_ZIP_RE.finditer(text):
        hood = TARGET_ZIPS.get(m.group(1))
        if hood:
            return hood
    return None


def _has_nontarget_city_zip(text: str) -> bool:
    """True if text contains an in-city ZIP that's NOT in our target list — a
    signal the listing is elsewhere in the city. Only meaningful when we have a
    target-ZIP list to compare against."""
    if not TARGET_ZIPS:
        return False
    for m in REGION_ZIP_RE.finditer(text):
        zip_code = m.group(1)
        if _zip_in_city(zip_code) and zip_code not in TARGET_ZIPS:
            return True
    return False


def _has_wrong_city_zip(text: str) -> bool:
    """True if the result names a same-region ZIP outside this metro."""
    for m in REGION_ZIP_RE.finditer(text):
        if not _zip_in_city(m.group(1)):
            return True
    return False


def _in_city(text: str) -> bool:
    """Whole-metro searches need *some* evidence the listing is in this city:
    a known hood, an in-city ZIP, or the city name in the text."""
    if _zip_neighborhood(text):
        return True
    for m in REGION_ZIP_RE.finditer(text):
        if _zip_in_city(m.group(1)):
            return True
    return CITY_LABEL.lower() in text.lower()


_STALE_MARKERS = re.compile(
    r"\b(rented|leased|deposit taken|off market|no longer available|"
    r"property is no longer|unavailable|wait[\s-]?list|waitlist)\b",
    re.IGNORECASE,
)

# Domains that appear in Exa results but aren't useful for rentals anywhere.
_BANNED_DOMAINS = {
    "thirdhome.com", "api.thirdhome.com",
    "airbnb.com", "vrbo.com", "homeaway.com", "vacasa.com",
    "hometogo.com", "booking.com", "hotels.com",
    "business.reddit.com",  # Reddit's corporate marketing, not user posts
    "redditinc.com",
    "loopnet.com",  # commercial real estate
    "crexi.com",   # commercial
}

# Indicator phrases that a snippet/title is actually about a rental listing.
_RENTAL_HINTS = re.compile(
    r"(\$\d[\d,]+|/mo\b|per month|for rent|to rent|available\s+\w+ \d|"
    r"\d\s*bed(room)?s?|\d\s*br\b|\d\s*bd\b|"
    r"sublet|sublease|lease starts|lease available|move[- ]in)",
    re.IGNORECASE,
)


def keep_basic(listing: Listing) -> bool:
    """Everything in keep() except the neighborhood gate: price, beds, baths,
    staleness, sale-vs-rent, house-mode, and 'is this actually a rental?'.

    Split out so the enrichment pass can run on candidates that pass these cheap
    checks *before* we read their detail page — the page is what tells us the
    real neighborhood/garage/bath, so the neighborhood gate runs afterward.
    """
    haystack = f"{listing.title} {listing.neighborhood or ''} {listing.snippet}"
    if BLOCKED_LOCATION_RE is not None and BLOCKED_LOCATION_RE.search(haystack):
        return False
    if _STALE_MARKERS.search(haystack):
        return False
    if listing.domain() in _BANNED_DOMAINS:
        return False
    if _has_wrong_city_zip(haystack):
        return False
    # Sale-price formatting in the snippet ($XXX,XXX or $X,XXX,XXX) → for-sale, not rent.
    if _SALE_PRICE_RE.search(haystack):
        return False
    # Title is a generic hub like "12 Apartments for Rent in Marina"
    if _HUB_TITLE_PATTERNS.search(listing.title):
        return False
    # House search: drop apartment/condo complexes and apartment hub pages.
    if LISTING_NOUN == "house" and (
        (_NON_HOUSE_RE.search(haystack) and not _HOUSE_RE.search(haystack))
        or _NON_HOUSE_URL_RE.search(listing.url)
    ):
        return False
    if listing.price is not None:
        if listing.price < MIN_PRICE or listing.price > MAX_PRICE:
            return False
    if listing.beds is not None:
        if listing.beds < MIN_BEDS or listing.beds > MAX_BEDS:
            return False
    # Bathrooms floor — only bites once a bath count is known (Craigslist
    # server-side filter, or the enrichment pass). Unknown bath = not dropped.
    if MIN_BATHROOMS and listing.bathrooms is not None and listing.bathrooms < MIN_BATHROOMS:
        return False

    # Search/direct/firecrawl results need to actually look like a rental
    # listing, not a generic page.
    if (
        listing.source.startswith("exa")
        or listing.source.startswith("direct:")
        or listing.source.startswith("firecrawl:")
    ):
        if not _RENTAL_HINTS.search(haystack):
            return False
        if listing.beds is None:
            return False
        # Reddit-source results must be a user post, not a corporate page.
        if listing.source == "exa-reddit" and "/r/" not in listing.url:
            return False
    # Owner-direct Reddit posts (reddit:<sub>): must read like a rental and live
    # on a real thread. Beds may be absent in free-text posts, so don't hard-drop
    # on a missing bed count the way the portal sources do.
    if listing.source.startswith("reddit:"):
        if not _RENTAL_HINTS.search(haystack):
            return False
        if "/r/" not in listing.url:
            return False
    return True


def keep(listing: Listing) -> bool:
    """Filter by price, beds, baths, neighborhood, staleness, and 'is this
    actually a rental listing?'. Runs keep_basic() then the neighborhood gate."""
    if not keep_basic(listing):
        return False

    haystack = f"{listing.title} {listing.neighborhood or ''} {listing.snippet}"
    matched_hood = listing.matches_neighborhood()

    if REQUIRE_NEIGHBORHOOD_MATCH:
        # An in-city ZIP outside our target list means it's elsewhere in the city — drop.
        if (
            _has_nontarget_city_zip(haystack)
            and _zip_neighborhood(haystack) is None
            and matched_hood is None
        ):
            return False
        # Require either a literal neighborhood match or a target ZIP.
        if matched_hood is None:
            return False
        return True

    # Whole-metro search: no required hood, but the listing must still be in this
    # city (a known hood, an in-city ZIP, or the city name in the text).
    return matched_hood is not None or _in_city(haystack)


# ---------- digest ----------

def render_markdown(
    new: list[Listing],
    total_seen: int,
    coverage_reports: list[SourceReport] | None = None,
) -> str:
    today = dt.date.today().isoformat()
    bed_label = f"{MIN_BEDS}BR" if MIN_BEDS == MAX_BEDS else f"{MIN_BEDS}-{MAX_BEDS}BR"
    lines = [f"# {CITY_LABEL} apartment hunt — {today}", ""]
    type_bit = f" · {LISTING_NOUN}" if LISTING_NOUN != "apartment" else ""
    if MAX_PRICE >= 50000:
        budget_bit = " · budget: any"
    elif IDEAL_MAX_PRICE == MAX_PRICE:
        budget_bit = f" · <= ${MAX_PRICE:,}/mo"
    else:
        budget_bit = (
            f" · ideal <= ${IDEAL_MAX_PRICE:,} "
            f"(${IDEAL_MAX_PRICE // NUM_PEOPLE:,}/person) · stretch <= ${MAX_PRICE:,}"
        )
    want = [label for label, _ in PREFERRED_KEYWORDS if label != LISTING_NOUN]
    want_bit = f" · want {', '.join(want)}" if want else ""
    criteria = f"**Criteria:** {bed_label}{type_bit}{budget_bit}{want_bit}"
    if MOVE_BY:
        criteria += f" · move by {MOVE_BY.isoformat()}"
    lines.append(criteria)
    if PREFERRED_NEIGHBORHOODS or FALLBACK_NEIGHBORHOODS:
        lines.append(
            f"**Top priority:** {', '.join(n.title() for n in PREFERRED_NEIGHBORHOODS) or '—'}  ·  "
            f"**Fallback:** {', '.join(n.title() for n in FALLBACK_NEIGHBORHOODS) or '—'}"
        )
    else:
        lines.append(f"**Coverage:** {CITY_LABEL} metro-wide")
    lines.append("")
    if not new:
        lines.append("_No new listings since last run._")
        lines.append("")
        lines.append(f"_(Tracking {total_seen} listings total.)_")
        _render_coverage(lines, coverage_reports or [])
        return "\n".join(lines)

    has_priority = bool(PREFERRED_NEIGHBORHOODS)
    preferred = [ls for ls in new if ls.is_preferred()] if has_priority else []
    other = [ls for ls in new if ls not in preferred]

    summary_bits = []
    if has_priority and preferred:
        pref_label = " / ".join(n.title() for n in PREFERRED_NEIGHBORHOODS)
        summary_bits.append(f"**{len(preferred)} in {pref_label}**")
    if other:
        where = "fallback neighborhoods" if has_priority else f"{CITY_LABEL}"
        summary_bits.append(f"{len(other)} in {where}")
    lines.append(" · ".join(summary_bits))
    lines.append("")

    if has_priority and preferred:
        lines.append(f"## Top picks — {' & '.join(n.title() for n in PREFERRED_NEIGHBORHOODS)}")
        lines.append("")
        _render_listings(lines, preferred)

    if other:
        lines.append(f"## {'Fallback neighborhoods' if has_priority else CITY_LABEL + ' ' + bed_label}")
        lines.append("")
        _render_listings(lines, other)

    lines.append(f"_(Tracking {total_seen} listings total.)_")
    _render_coverage(lines, coverage_reports or [])
    return "\n".join(lines)


def _render_listings(lines: list[str], listings: list[Listing]) -> None:
    for ls in sorted(listings, key=lambda x: (_neighborhood_rank(x), -len(x.feature_matches()), x.price or 99999)):
        price = f"${ls.price:,}" if ls.price else "price n/a"
        if ls.price is not None and ls.price > IDEAL_MAX_PRICE:
            price += " stretch"
        beds = f"{ls.beds}BR" if ls.beds else "?BR"
        # For Craigslist, show the raw location the seller typed. For Exa, show
        # the matched canonical neighborhood.
        if ls.source == "craigslist" and ls.neighborhood:
            hood_label = ls.neighborhood
        else:
            hood_label = (ls.matches_neighborhood() or ls.neighborhood or "?").title()
        source_tag = f" _[{ls.source} · {ls.domain()}]_"
        lines.append(f"- **[{ls.title}]({ls.url})**{source_tag}")
        bath_bit = f" · {ls.bathrooms:g}BA" if ls.bathrooms is not None else ""
        sqft_bit = f" · {ls.sqft:,} sqft" if ls.sqft else ""
        lines.append(f"  · {price} · {beds}{bath_bit}{sqft_bit} · {hood_label}")
        if ls.garage_status == "confirmed":
            lines.append("  · ✓ garage")
        elif ls.garage_status == "none found":
            lines.append("  · ✗ no garage found")
        elif ENRICH_DETAILS:
            lines.append("  · ? garage unverified")
        # Soft keyword features not covered by the enriched fields.
        feats = [f for f in ls.feature_matches() if f not in {"house", "garage", "office", "2 bath"}]
        if ls.has_office:
            feats = ["office"] + feats
        if feats:
            lines.append(f"  · ✓ {' · '.join(feats)}")
        if ls.snippet:
            lines.append(f"  · _{ls.snippet[:220]}_")
        lines.append("")


def _render_coverage(lines: list[str], reports: list[SourceReport]) -> None:
    if not reports:
        return

    ok = sum(1 for report in reports if report.status == "ok")
    blocked = sum(1 for report in reports if report.status == "blocked")
    errors = len(reports) - ok - blocked

    lines.append("")
    lines.append("## Direct source coverage")
    lines.append("")
    lines.append(
        f"Checked {len(reports)} public source pages directly: "
        f"{ok} reachable, {blocked} blocked/rate-limited, {errors} errored."
    )
    lines.append("Sites with 0 parsed candidates may still be JS-only, empty, or covered through Exa.")
    lines.append("")

    for report in reports:
        label = report.source.removeprefix("direct:")
        count = f"{report.listings} raw candidate{'s' if report.listings != 1 else ''}"
        note = f" — {report.note}" if report.note else ""
        lines.append(f"- **{label}**: {report.status}, {count}{note}")
    lines.append("")


def _neighborhood_rank(ls: Listing) -> int:
    match = ls.matches_neighborhood()
    ranked = list(PREFERRED_NEIGHBORHOODS) + list(FALLBACK_NEIGHBORHOODS)
    order = {hood: i for i, hood in enumerate(ranked)}
    return order.get(match or "", 99)


def _dedupe_key(ls: Listing) -> str:
    if ls.source == "craigslist":
        return ls.id
    return _normalized_url(ls.url)


# Street-address parser for cross-source dedup. URL dedup can't catch the same
# physical house listed on two different sites (e.g. Zillow + Highrises) — those
# have different URLs but the same street address. This collapses by address.
_STREET_TYPES = (
    r"st|street|ave|avenue|blvd|boulevard|way|pl|place|dr|drive|"
    r"ct|court|ln|lane|rd|road|ter|terrace|cir|circle|pkwy|parkway|trl|trail"
)
_ADDRESS_RE = re.compile(
    rf"\b(\d+)\s+"                                   # house number
    rf"(?:(?:n|s|e|w|north|south|east|west)\.?\s+)?"  # optional directional (ignored)
    rf"([a-z0-9][a-z0-9 ]*?)\s+"                      # street name
    rf"(?:{_STREET_TYPES})\b",                        # street type
    re.IGNORECASE,
)
_ZIP_RE = re.compile(r"\b(8\d{4})\b")  # Colorado ZIPs start with 8


def _address_key(ls: Listing) -> str | None:
    """A normalized (house number, street name, ZIP) key, or None if no address
    is parseable. Directional (N/S/E/W) is intentionally dropped: the same house
    shows up as "238 Harrison St" on one source and "238 N Harrison St" on
    another. The ZIP keeps that from colliding two genuinely different streets."""
    text = f"{ls.title} {ls.snippet}"
    m = _ADDRESS_RE.search(text)
    if not m:
        return None
    number = m.group(1)
    street = re.sub(r"\s+", " ", m.group(2).strip().lower())
    zip_m = _ZIP_RE.search(text)
    zip_code = zip_m.group(1) if zip_m else ""
    return f"{number}|{street}|{zip_code}"


# Garage confidence, highest first. "confirmed" is positive evidence from a
# detail page; "none found" only means that one page didn't mention parking;
# None means unverified. When two sources disagree, trust the positive signal.
_GARAGE_RANK = {"confirmed": 2, "none found": 1, None: 0}


def collapse_by_address(listings: list[Listing]) -> list[Listing]:
    """Collapse listings that share a street address into one card, keeping the
    richest representative and merging the garage signal (confirmed > none found
    > unverified). Listings with no parseable address (e.g. Craigslist posts
    titled 'Wash Park house for rent') are passed through untouched."""
    groups: dict[str, list[Listing]] = {}
    passthrough: list[Listing] = []
    for ls in listings:
        key = _address_key(ls)
        if key is None:
            passthrough.append(ls)
        else:
            groups.setdefault(key, []).append(ls)

    out: list[Listing] = []
    for group in groups.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        # Best garage signal across the group.
        best_garage = max(
            (g.garage_status for g in group),
            key=lambda s: _GARAGE_RANK.get(s, 0),
        )
        # Representative: prefer the one already carrying the best garage signal,
        # then the most-enriched, then the cheapest known price.
        rep = max(group, key=lambda g: (
            _GARAGE_RANK.get(g.garage_status, 0),
            1 if g.enriched else 0,
            -(g.price or 99999),
        ))
        rep.garage_status = best_garage
        # Backfill any missing detail fields from the duplicates.
        for g in group:
            if g is rep:
                continue
            if rep.price is None:
                rep.price = g.price
            if rep.beds is None:
                rep.beds = g.beds
            if rep.bathrooms is None:
                rep.bathrooms = g.bathrooms
            if rep.sqft is None:
                rep.sqft = g.sqft
        out.append(rep)

    return out + passthrough


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--city", default="sf", choices=sorted(profiles.PROFILES),
        help="which city profile to search (default: sf)",
    )
    parser.add_argument("--dry", action="store_true", help="write markdown but do not update seen-set")
    parser.add_argument("--reset", action="store_true", help="clear seen-set and exit")
    args = parser.parse_args()

    apply_profile(profiles.get_profile(args.city))
    print(f"City: {CITY_LABEL} ({ACTIVE_PROFILE.key})")

    if args.reset:
        if SEEN_PATH.exists():
            SEEN_PATH.unlink()
        print(f"seen-set cleared for {CITY_LABEL}.")
        return 0

    # Load env from project dir.
    load_dotenv(ROOT / ".env")

    exa_key = os.environ.get("EXA_API_KEY")
    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY")

    if not exa_key:
        print("ERROR: set EXA_API_KEY in .env", file=sys.stderr)
        return 1

    print("Fetching Craigslist…")
    cl = fetch_craigslist()
    print(f"  {len(cl)} raw listings")

    print("Fetching direct public sources…")
    direct, coverage_reports = fetch_direct_public_sources()
    print(f"  {len(direct)} raw listing candidates")
    print(f"  {len(coverage_reports)} direct source pages checked")

    firecrawl_routed: list[Listing] = []
    if firecrawl_key:
        print(f"Fetching {len(FIRECRAWL_SEEDS)} JS-only / blocked sources via Firecrawl…")
        firecrawl_routed, fc_reports = fetch_firecrawl_sources(firecrawl_key)
        print(f"  {len(firecrawl_routed)} raw listing candidates")
        coverage_reports.extend(fc_reports)
    else:
        print(f"Skipping {len(FIRECRAWL_SEEDS)} Firecrawl-routed sources: FIRECRAWL_API_KEY not set in .env")
        for domain, url in FIRECRAWL_SEEDS:
            coverage_reports.append(SourceReport(
                source=f"firecrawl:{domain}",
                url=url,
                status="skipped",
                note="FIRECRAWL_API_KEY not configured",
            ))

    print("Fetching Exa…")
    exa = fetch_exa(exa_key)
    print(f"  {len(exa)} raw listings")

    print("Fetching Reddit owner-direct (reddit-cli)…")
    reddit, reddit_reports = fetch_reddit()
    print(f"  {len(reddit)} raw listing candidates")
    coverage_reports.extend(reddit_reports)

    zillow: list[Listing] = []
    if firecrawl_key:
        print("Fetching Zillow via Firecrawl…")
        zillow, zillow_report = fetch_zillow_firecrawl(firecrawl_key)
        print(f"  {len(zillow)} raw listings ({zillow_report.status}: {zillow_report.note})")
        coverage_reports.append(zillow_report)
    else:
        print("Skipping Zillow: FIRECRAWL_API_KEY not set in .env")
        coverage_reports.append(SourceReport(
            source="zillow",
            url=ZILLOW_RENTAL_URL,
            status="skipped",
            note="FIRECRAWL_API_KEY not configured",
        ))

    all_listings = cl + direct + firecrawl_routed + exa + zillow + reddit

    # Dedup within this run (Exa often surfaces the same URL across queries).
    by_id: dict[str, Listing] = {}
    for ls in all_listings:
        by_id.setdefault(_dedupe_key(ls), ls)
    deduped = list(by_id.values())

    # Apply criteria filter. When enrichment is on, read each surviving
    # candidate's detail page first so the final filter (garage/bath/ring) runs
    # on real page data, not thin snippets.
    if ENRICH_DETAILS and firecrawl_key:
        basic = [ls for ls in deduped if keep_basic(ls)]
        print(f"  {len(basic)} candidates pass basic filter")
        enrich_candidates(firecrawl_key, basic)
        matched = [ls for ls in basic if keep(ls)]
    else:
        if ENRICH_DETAILS and not firecrawl_key:
            print("  enrichment skipped: FIRECRAWL_API_KEY not set")
        matched = [ls for ls in deduped if keep(ls)]
    print(f"  {len(matched)} match criteria (post-filter)")

    # Diff against seen.
    seen = load_seen()
    new = [ls for ls in matched if ls.id not in seen]
    print(f"  {len(new)} new since last run")

    # Render + write digest.
    md = render_markdown(new, total_seen=len(seen) + len(new), coverage_reports=coverage_reports)
    DIGEST_PATH.write_text(md)
    print(f"Wrote {DIGEST_PATH}")

    if args.dry:
        # Dry runs only update digest_latest.md, NEVER the dated archive — otherwise an
        # ad-hoc dry run clobbers the morning cron's archived digest for the same date.
        print("--dry: skipping seen-set update and dated archive")
    else:
        # Archive a dated copy from real (non-dry) runs only.
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_DIR / f"{dt.date.today().isoformat()}.md").write_text(md)
        seen.update(ls.id for ls in matched)
        save_seen(seen)
        print(f"Updated seen-set at {SEEN_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
