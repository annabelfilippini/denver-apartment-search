"""Per-city search profiles for the apartment hunt.

Everything that varies by metro lives here: criteria, neighborhoods, ZIP rules,
every source URL, Zillow map bounds, and the strings the Exa neural sweep uses.
`apartment_hunt.py` picks one profile (via --city) and runs the same pipeline
against it.

To add a city, copy an existing SearchProfile, change the URLs/labels, and add
it to PROFILES. Source URLs that turn out wrong just show up as "error/blocked"
in the coverage report; Craigslist, Zillow, and the Exa city sweep are the
robust backbone that don't depend on hand-tuned aggregator URLs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchProfile:
    key: str                       # "sf", "denver" — used for filenames
    city_label: str                # "San Francisco"
    state_label: str               # "California"

    # Criteria
    min_price: int
    ideal_max_price: int
    max_price: int                 # stretch ceiling
    min_beds: int
    max_beds: int
    num_people: int                # for the $/person line
    move_by: dt.date | None        # cosmetic, shown in the header; not a filter

    # Neighborhoods (lowercase). `neighborhoods` is the full label set used to
    # recognize a hood in listing text. preferred floats to the top; fallback
    # sits below. When both are empty the digest is a single price-sorted list.
    neighborhoods: tuple[str, ...]
    preferred_neighborhoods: tuple[str, ...]
    fallback_neighborhoods: tuple[str, ...]
    cl_location_to_hood: dict      # Craigslist's seller-typed location -> canonical hood
    require_neighborhood_match: bool  # SF: must match a target hood. Denver: whole metro.

    # ZIP handling
    target_zips: dict              # zip -> hood, for results that omit the hood name
    city_zip_prefixes: tuple[str, ...]  # 3-digit prefixes that mean "in this metro"
    region_zip_pattern: str        # regex for a plausible same-state ZIP

    # Sources
    craigslist_base: str
    direct_source_seeds: tuple[tuple[str, str], ...]
    firecrawl_seeds: tuple[tuple[str, str], ...]
    property_manager_domains: tuple[str, ...]
    zillow_rental_url: str
    zillow_search_term: str
    zillow_map_bounds: dict
    exa_neighborhoods: tuple[str, ...]  # subset to sweep per-hood; empty -> one city-wide query
    # Subreddits to sweep for owner-direct rental posts via the local reddit-cli.
    # Empty -> Reddit channel is skipped for this city. Default empty so a profile
    # that doesn't opt in (e.g. SF) never pulls another metro's Reddit.
    reddit_subreddits: tuple[str, ...] = field(default_factory=tuple)

    # Filtering: hood names that are an automatic "no" (regex-ready). Empty = none.
    blocked_location_markers: tuple[str, ...] = field(default_factory=tuple)

    # Property type word used in the Exa free-text queries ("apartment", "house").
    listing_noun: str = "apartment"
    # Hard minimum bathrooms. 0 = no bath filter. Enforced once a listing's
    # bath count is known (Craigslist server-side, or the enrichment pass).
    min_bathrooms: int = 0
    # When True, each candidate that survives the cheap filters has its full
    # detail page fetched (Firecrawl) and parsed for garage/bath/bed/office/sqft
    # before the final filter + ranking. This is what makes "if a garage isn't
    # mentioned, it doesn't have one" reliable — we read the whole page, not a
    # thin search snippet. Costs Firecrawl credits per survivor.
    enrich_details: bool = False
    # Extra params merged into the Craigslist search query (e.g. housing_type=6
    # for houses, min_bathrooms=2). These are real source-side filters.
    craigslist_extra_params: tuple[tuple[str, object], ...] = field(default_factory=tuple)
    # Soft, scored "nice to have" features. Each entry is (label, match-variants).
    # A listing is never dropped for missing these; they rank it up and show as
    # ✓ badges so you can see which requirements each listing meets.
    preferred_keywords: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# San Francisco — Annabel's 3BR hunt. Unchanged behavior from the original.
# --------------------------------------------------------------------------- #

SF = SearchProfile(
    key="sf",
    city_label="San Francisco",
    state_label="California",
    min_price=3200,
    ideal_max_price=7500,   # $2,500/person for 3 people
    max_price=9000,         # stretch ceiling
    min_beds=3,
    max_beds=3,
    num_people=3,
    move_by=dt.date(2026, 6, 15),
    neighborhoods=(
        "russian hill", "north beach", "hayes valley", "marina",
        "pacific heights", "cow hollow", "nob hill",
    ),
    preferred_neighborhoods=("russian hill", "north beach"),
    fallback_neighborhoods=(
        "hayes valley", "marina", "pacific heights", "cow hollow", "nob hill",
    ),
    cl_location_to_hood={
        "north beach": "north beach",
        "north beach / telegraph hill": "north beach",
        "telegraph hill": "north beach",
        "russian hill": "russian hill",
        "marina": "marina",
        "marina / cow hollow": "marina",
        "cow hollow": "cow hollow",
        "nob hill": "nob hill",
        "russian hill / nob hill": "nob hill",
        "pacific heights": "pacific heights",
        "lower pacific heights": "pacific heights",
        "pac hts": "pacific heights",
        "japantown": "pacific heights",
        "hayes valley": "hayes valley",
    },
    require_neighborhood_match=True,
    target_zips={
        "94133": "north beach",
        "94123": "marina",
        "94115": "pacific heights",
    },
    city_zip_prefixes=("941",),
    region_zip_pattern=r"\b(9\d{4})\b",
    craigslist_base="https://sfbay.craigslist.org/search/sfc/apa",
    direct_source_seeds=(
        ("apartmentguide.com", "https://www.apartmentguide.com/apartments/California/San-Francisco/3-beds-1z141xs/"),
        ("homefinder.com", "https://homefinder.com/rentals/CA/San-Francisco?beds=3"),
        ("redfin.com", "https://www.redfin.com/city/17151/CA/San-Francisco/apartments-for-rent/filter/property-type=apartment,min-beds=3,max-beds=3"),
        ("rentable.co", "https://www.rentable.co/san-francisco-ca?beds=3"),
        ("rentberry.com", "https://rentberry.com/apartments/s/san-francisco-ca/3-bed"),
        ("rentcafe.com", "https://www.rentcafe.com/3-bedroom-apartments-for-rent/us/ca/san-francisco/"),
        ("rentsfnow.com", "https://www.rentsfnow.com/apartments-for-rent/san-francisco/"),
        ("structureproperties.com", "https://structureproperties.com/available-rentals/"),
    ),
    firecrawl_seeds=(
        ("apartments.com", "https://www.apartments.com/san-francisco-ca/3-bedrooms/"),
        ("apartmentfinder.com", "https://www.apartmentfinder.com/California/San-Francisco-Apartments/3-Bedrooms"),
        ("apartmenthomeliving.com", "https://www.apartmenthomeliving.com/san-francisco-ca/apartments-for-rent/3-bedroom"),
        ("apartmentlist.com", "https://www.apartmentlist.com/ca/san-francisco?beds=3"),
        ("equityapartments.com", "https://www.equityapartments.com/san-francisco-apartments"),
        ("forrent.com", "https://www.forrent.com/find/CA/metro-San+Francisco/San+Francisco/beds-3"),
        ("hotpads.com", "https://hotpads.com/san-francisco-ca/3-bedroom-apartments-for-rent"),
        ("realtor.com", "https://www.realtor.com/apartments/San-Francisco_CA/beds-3"),
        ("trulia.com", "https://www.trulia.com/for_rent/San_Francisco,CA/3p_beds/"),
        ("avaloncommunities.com", "https://www.avaloncommunities.com/california/san-francisco-apartments"),
        ("compass.com", "https://www.compass.com/for-rent/san-francisco-ca/3-bedrooms/"),
        ("padmapper.com", "https://www.padmapper.com/apartments/san-francisco-ca/3-beds"),
        ("rent.com", "https://www.rent.com/california/san-francisco-apartments/3-bedroom"),
        ("zumper.com", "https://www.zumper.com/apartments-for-rent/san-francisco-ca/3-beds"),
        ("chandlerproperties.com", "https://chandlerproperties.com/"),
        ("gaetanirealestate.com", "https://www.gaetanirealestate.com/vacancies"),
        ("jwavro.com", "https://www.jwavro.com/rentals.php"),
        ("sfcityrents.com", "https://www.sfcityrents.com/"),
        ("trinitysf.com", "https://www.trinitysf.com/"),
        ("yeeproperties.com", "https://www.yeeproperties.com/vacancies"),
    ),
    property_manager_domains=(
        "anchorrealtyinc.com", "brickandtimber.com", "chandlerproperties.com",
        "gaetanirealestate.com", "jwavro.com", "kinetic-re.com",
        "laphamcompany.com", "rentsfnow.com", "sfcityrents.com",
        "structureproperties.com", "trinitysf.com", "yeeproperties.com",
    ),
    zillow_rental_url="https://www.zillow.com/san-francisco-ca/rentals/",
    zillow_search_term="San Francisco, CA",
    zillow_map_bounds={"west": -122.5183, "east": -122.3551, "south": 37.7080, "north": 37.8324},
    exa_neighborhoods=(
        "russian hill", "north beach", "hayes valley", "marina",
        "pacific heights", "cow hollow", "nob hill",
    ),
    blocked_location_markers=(
        "tenderloin", "tendernob", "tender nob", "lower nob", "lower nob hill",
        "polk gulch", "civic center", "south beach",
    ),
)


# --------------------------------------------------------------------------- #
# Denver — whole-metro 3BR, wide budget. Edit criteria/neighborhoods to taste.
# --------------------------------------------------------------------------- #

# Recognized Denver neighborhoods, for labeling/sorting only. Nothing is dropped
# for lacking a match (require_neighborhood_match=False), so this is a metro-wide
# search that just shows a nicer hood label when it can find one.
_DENVER_HOODS = (
    "lohi", "lower highlands", "highland", "highlands", "west highland",
    "sunnyside", "berkeley", "sloan's lake", "sloans lake", "jefferson park",
    "capitol hill", "cap hill", "cheesman park", "city park", "city park west",
    "congress park", "cherry creek", "cherry creek north", "washington park",
    "wash park", "baker", "platt park", "rino", "river north", "five points",
    "uptown", "golden triangle", "union station", "lodo", "ballpark", "speer",
    "whittier", "cole", "country club", "hilltop", "park hill", "central park",
    "virginia village", "university", "observatory park", "lincoln park",
    # Cherry Creek ~10-min drive ring
    "hale", "mayfair", "montclair", "crestmoor", "belcaro", "bonnie brae",
    "cory-merrill", "cory merrill", "glendale", "cherry hills",
    "cherry hills village", "polo club", "lowry",
)

# The hard allow-list when the Denver hunt is locked to Cherry Creek + the
# ~10-minute drive ring (require_neighborhood_match=True). A listing must match
# one of these labels — or a ring ZIP — to survive. Includes the common spelling
# variants so text matching catches them. Anything outside this set is dropped,
# not just sorted down.
_DENVER_RING_HOODS = (
    "cherry creek", "cherry creek north", "hilltop", "hale", "mayfair",
    "montclair", "crestmoor", "belcaro", "country club", "congress park",
    "washington park", "wash park", "bonnie brae", "cory-merrill",
    "cory merrill", "observatory park", "glendale", "cherry hills",
    "cherry hills village", "polo club", "lowry", "virginia village",
    # +1-mile ring widen (2026-06-19): immediately-adjacent hoods now inside the
    # widened map bounds. Still NOT metro-wide — these abut the original ring.
    "cheesman park", "city park", "city park west", "capitol hill", "cap hill",
    "park hill", "south park hill", "north park hill", "platt park", "baker",
    "university", "university park", "wellshire", "windsor",
)

DENVER = SearchProfile(
    key="denver",
    city_label="Denver",
    state_label="Colorado",
    # Cap at $5,000/mo. Small floor only to drop $0/data errors.
    min_price=1000,
    ideal_max_price=5000,
    max_price=5000,
    min_beds=3,
    max_beds=3,
    num_people=3,
    move_by=None,
    # Locked to the ring: only ring labels are recognized, so a listing that
    # only mentions a non-ring hood (e.g. "highlands") no longer slips in.
    neighborhoods=_DENVER_RING_HOODS,
    # Cherry Creek itself floats to the very top; the ~10-min drive ring sits in
    # the fallback band. require_neighborhood_match is True, so houses outside
    # the ring are dropped entirely rather than shown under "Elsewhere".
    preferred_neighborhoods=("cherry creek", "cherry creek north"),
    fallback_neighborhoods=(
        "hilltop", "hale", "mayfair", "crestmoor", "belcaro", "country club",
        "congress park", "washington park", "bonnie brae", "cory-merrill",
        "observatory park", "glendale", "cherry hills", "polo club", "lowry",
        # +1-mile ring widen (2026-06-19) — adjacent band, below Cherry Creek.
        "cheesman park", "city park", "city park west", "capitol hill",
        "park hill", "south park hill", "north park hill", "platt park",
        "baker", "university", "university park", "wellshire", "windsor",
    ),
    cl_location_to_hood={
        "lohi": "lohi",
        "lower highlands": "lohi",
        "highlands": "highlands",
        "highland": "highlands",
        "berkeley": "berkeley",
        "sloans lake": "sloan's lake",
        "sloan's lake": "sloan's lake",
        "capitol hill": "capitol hill",
        "cap hill": "capitol hill",
        "city park": "city park",
        "congress park": "congress park",
        "cherry creek": "cherry creek",
        "cherry creek north": "cherry creek north",
        "washington park": "washington park",
        "wash park": "washington park",
        "baker": "baker",
        "platt park": "platt park",
        "rino": "rino",
        "five points": "five points",
        "uptown": "uptown",
        "lodo": "lodo",
        "park hill": "park hill",
        "central park": "central park",
        "university": "university",
        "hilltop": "hilltop",
        "hale": "hale",
        "mayfair": "mayfair",
        "montclair": "montclair",
        "crestmoor": "crestmoor",
        "belcaro": "belcaro",
        "country club": "country club",
        "congress park": "congress park",
        "bonnie brae": "bonnie brae",
        "cory-merrill": "cory-merrill",
        "cory merrill": "cory-merrill",
        "observatory park": "observatory park",
        "glendale": "glendale",
        "cherry hills": "cherry hills",
        "cherry hills village": "cherry hills",
        "lowry": "lowry",
        # +1-mile ring widen (2026-06-19)
        "cheesman park": "cheesman park",
        "city park": "city park",
        "city park west": "city park west",
        "park hill": "park hill",
        "south park hill": "south park hill",
        "north park hill": "north park hill",
        "platt park": "platt park",
        "baker": "baker",
        "university": "university",
        "university park": "university park",
        "wellshire": "wellshire",
        "windsor": "windsor",
    },
    require_neighborhood_match=True,
    # Ring ZIPs only. Used to rescue results whose text gives a ZIP but no hood
    # name. Non-ring Denver ZIPs are deliberately absent — a listing on one of
    # them counts as "elsewhere" and is dropped.
    target_zips={
        "80206": "cherry creek",
        "80220": "hilltop",
        "80209": "washington park",
        "80210": "observatory park",
        "80218": "country club",
        "80246": "glendale",
        "80222": "virginia village",
        "80224": "virginia village",
        "80113": "cherry hills",
        "80230": "lowry",
        # +1-mile ring widen (2026-06-19) — adjacent ZIPs now inside the bounds.
        "80203": "capitol hill",
        "80205": "city park",
        "80207": "park hill",
        "80247": "windsor",
    },
    city_zip_prefixes=("800", "801", "802"),
    region_zip_pattern=r"\b(8\d{4})\b",
    craigslist_base="https://denver.craigslist.org/search/apa",
    direct_source_seeds=(
        ("homefinder.com", "https://homefinder.com/rentals/CO/Denver?beds=3&property_type=house"),
        ("rentable.co", "https://www.rentable.co/denver-co?beds=3&property_types=house"),
        ("rentberry.com", "https://rentberry.com/apartments/s/denver-co/3-bed/houses"),
    ),
    firecrawl_seeds=(
        # Big portals, house-typed, pointed at Cherry Creek / Denver.
        ("zillow.com", "https://www.zillow.com/cherry-creek-denver-co/houses/3-bedrooms/"),
        ("trulia.com", "https://www.trulia.com/for_rent/Denver,CO/3p_beds/SINGLE-FAMILY_HOME_type/"),
        ("realtor.com", "https://www.realtor.com/apartments/Denver_CO/beds-3/type-single-family-home"),
        ("hotpads.com", "https://hotpads.com/denver-co/houses-for-rent?beds=3"),
        ("redfin.com", "https://www.redfin.com/city/5155/CO/Denver/apartments-for-rent/filter/property-type=house,min-beds=3"),
        ("apartments.com", "https://www.apartments.com/houses/denver-co/3-bedrooms/"),
        ("zumper.com", "https://www.zumper.com/houses-for-rent/denver-co/3-beds"),
        ("rent.com", "https://www.rent.com/colorado/denver-houses/3-bedroom"),
        ("compass.com", "https://www.compass.com/for-rent/denver-co/3-bedrooms/"),
        ("padmapper.com", "https://www.padmapper.com/apartments/denver-co/3-beds/type-house"),
        # More house-focused portals.
        ("homes.com", "https://www.homes.com/denver-co/houses-for-rent/3-bedrooms/"),
        ("dwellsy.com", "https://www.dwellsy.com/homes/denver-co/3-bedroom"),
        ("rentals.com", "https://www.rentals.com/Colorado/Denver/houses/3-bedrooms/"),
        ("renterswarehouse.com", "https://www.renterswarehouse.com/homes-for-rent/co/denver"),
        # Institutional single-family landlords — large Denver-metro SFH inventory,
        # nearly all with garages. Ring filter keeps only the Cherry-Creek-area ones.
        ("invitationhomes.com", "https://www.invitationhomes.com/denver/homes-for-rent"),
        ("amh.com", "https://www.amh.com/homes-for-rent/co/denver"),
        ("msrenewal.com", "https://msrenewal.com/homes-for-rent/colorado/denver-metro"),
        ("triconresidential.com", "https://triconresidential.com/find-a-home/?location=Denver,+CO"),
        # More institutional SFH landlords (added 2026-06-20). Same pattern: own-site
        # inventory that often never syndicates to Zillow. Ring filter trims to area.
        ("rentprogress.com", "https://rentprogress.com/houses-for-rent/colorado/denver-metro"),
        ("firstkeyhomes.com", "https://www.firstkeyhomes.com/houses-for-rent/co"),
        ("homeriver.com", "https://www.homeriver.com/denver-property-management/denver-homes-for-rent"),
        ("mynd.co", "https://www.mynd.co/homes-for-rent/co/denver"),
        ("poplarhomes.com", "https://www.poplarhomes.com/homes-for-rent/co/denver"),
        ("pathlighthomes.com", "https://pathlighthomes.com/homes-for-rent?market=Denver"),
        # By-owner / FRBO channels (added 2026-06-20) — owner-direct rentals that
        # skip the big portals. Zillow's for-rent-by-owner slice + a dedicated FRBO site.
        ("forrentbyowner.com", "https://www.forrentbyowner.com/denver-co/"),
        ("zillow.com/fsbo", "https://www.zillow.com/cherry-creek-denver-co/houses/fsba_lt/3-bedrooms/"),
        # Self-tour / self-showing platforms (added 2026-06-20) — the genuinely hidden
        # small-PM SFH inventory. BEST-EFFORT: these are mostly per-PM booking widgets
        # with no clean city-browse URL, so they often return 0. Kept as seeds so any
        # that DO expose a Denver search get swept; the real win here will be seeding
        # specific Denver PM pages that use them (fast-follow).
        ("rently.com", "https://rently.com/properties?location=Denver%2C+CO"),
        ("showmojo.com", "https://showmojo.com/listings/map?location=Denver,+CO"),
        ("tenantturner.com", "https://app.tenantturner.com/listings?location=Denver,+CO"),
    ),
    # Local Denver property managers that list single-family rentals. These feed
    # the Exa per-manager sweep; a wrong/dead domain simply returns nothing.
    property_manager_domains=(
        "realatlas.com", "evernest.co", "rpmcolorado.com", "rentgrace.com",
        "coloradorpm.com", "allcountydenver.com", "pmidenver.com",
        "foxpropertymgmt.com", "milehighpm.com",
    ),
    zillow_rental_url="https://www.zillow.com/cherry-creek-denver-co/houses/rentals/",
    zillow_search_term="Cherry Creek, Denver, CO",
    # ~10-min drive ring around Cherry Creek (Wash Park west → Lowry east,
    # Country Club north → Cherry Hills south), WIDENED by ~1 mile on every side
    # (2026-06-19). At ~39.7°N, 1 mi ≈ 0.019° lon / 0.0145° lat.
    zillow_map_bounds={"west": -105.02, "east": -104.86, "south": 39.645, "north": 39.775},
    exa_neighborhoods=(
        "cherry creek", "cherry creek north", "hilltop", "hale",
        "washington park", "country club", "congress park", "glendale",
        # +1-mile ring widen (2026-06-19)
        "cheesman park", "city park", "park hill", "platt park", "university",
    ),
    # Owner-direct Reddit sweep (added 2026-06-20). r/DenverList is the local
    # classifieds sub; r/Denver and r/Colorado carry occasional "renting my house"
    # posts. The ring filter drops anything not in/near Cherry Creek automatically.
    reddit_subreddits=("DenverList", "Denver", "Colorado"),
    blocked_location_markers=(),
    listing_noun="house",
    min_bathrooms=2,
    enrich_details=True,
    # House-only + 2-bath, applied server-side on Craigslist (housing_type 6 = house).
    craigslist_extra_params=(("housing_type", 6), ("min_bathrooms", 2)),
    preferred_keywords=(
        ("house", ("house", "single-family", "single family", "detached")),
        ("garage", ("garage", "2-car", "2 car", "two-car", "two car", "attached garage", "carport")),
        ("office", ("office", "den", "study", "bonus room", "flex room", "workspace")),
        ("open floor plan", ("open floor", "open-concept", "open concept", "open plan", "open-plan", "open layout")),
        ("2 bath", ("2 bath", "2 ba", "2ba", "two bath", "2.5 bath", "2.5 ba", "2 full bath", "3 bath", "3 ba")),
    ),
)


PROFILES: dict[str, SearchProfile] = {
    SF.key: SF,
    DENVER.key: DENVER,
}


def get_profile(key: str) -> SearchProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise SystemExit(
            f"unknown city {key!r}. available: {', '.join(sorted(PROFILES))}"
        )
