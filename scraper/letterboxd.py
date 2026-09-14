"""Resolves a Cineteca film to its Letterboxd entry and average rating.

Uses `api.letterboxd.com/api/v0/search`, which answers unauthenticated with a
`FilmSummary` per hit carrying `name`, `originalName`, `releaseYear`,
`runTime`, `directors[]` and `rating` — everything needed to both confirm a
match and read the score, in one request. The website's own search
(`letterboxd.com/s/search/`) is Cloudflare-challenged and 403s anything
without a browser TLS fingerprint, which is why letterboxdpy carries curl-cffi
and why this module does not use it.

The one rule that matters here: **never guess**. Showing a stranger's rating
against a film is worse than showing nothing, so a candidate is accepted only
when independent fields agree, and rejected outright otherwise. There is
deliberately no "best scoring result wins" fallback.

Everything in this module is optional data. Failures raise, and scrape.py is
responsible for making sure they can never take a run down.
"""
import json
import logging
import re
import time
import unicodedata
import urllib.parse

import requests

SEARCH_URL = "https://api.letterboxd.com/api/v0/search"
PER_PAGE = 8

# Cineteca cuts a title at exactly 50 characters. Every title that long in the
# data is a truncated shorts programme ("Hilando fronteras/Inmigrante/…"), and
# the longest genuine film title is 48, so the cut is its own marker. A
# programme of several shorts has no single rating to show.
TITLE_TRUNCATED_AT = 50

# Cineteca lists the same film twice when it screens both dubbed and subtitled.
# The suffix is an exhibition format, not part of the title.
FORMAT_SUFFIX_RE = re.compile(
    r"(?:\s+(?:DOB|SUB|DOBLADA|SUBTITULADA|4K|2D|3D))+\s*$", re.I
)

YEAR_SLACK = 1  # revivals carry the re-release year, not the production year
RUNTIME_SLACK = 3  # minutes; sources round differently

MAX_RETRIES = 2  # optional data — fail fast rather than hold the run open
TIMEOUT = 15
USER_AGENT = (
    "CarteleraCinetecaBot/1.0 (+https://github.com/heliouz/cartelera-cineteca)"
)

log = logging.getLogger("scrape.letterboxd")


def fold(value):
    """Accent- and case-folded, punctuation flattened, for comparison only."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def strip_format_suffix(title):
    return FORMAT_SUFFIX_RE.sub("", title or "").strip()


def query_titles(film):
    """Titles to search, best first, deduped.

    The original title goes first because it is what Letterboxd indexes as the
    primary name most often. The Spanish title is the fallback that recovers
    films whose original title is a transliteration Letterboxd spells
    differently — "Lo que queda de ti" is findable, its `original_title`
    "Allly baqi mink" is not.
    """
    titles = []
    for raw in (film.get("original_title"), film.get("title")):
        candidate = strip_format_suffix(raw)
        if candidate and candidate not in titles:
            titles.append(candidate)
    return titles


def is_skippable(film):
    """True for records that cannot have a single Letterboxd entry."""
    title = film.get("title") or ""
    if len(title) >= TITLE_TRUNCATED_AT:
        return True
    if (film.get("director") or "").strip().lower() == "varios":
        return True
    return not query_titles(film)


def directors_agree(ours, theirs):
    """Whether any distinctive name token is shared.

    Token overlap rather than string equality: Cineteca and Letterboxd disagree
    on accents ("Mendizábal"/"Mendizabal"), on name order, and on how many of a
    Spanish double surname they keep. Tokens of 4+ characters only, so that
    "de", "la", "van" and initials cannot carry a match on their own.
    """
    if not ours or not theirs:
        return False
    mine = {t for t in fold(ours).split() if len(t) > 3}
    yours = set()
    for person in theirs:
        yours |= {t for t in fold(person.get("name", "")).split() if len(t) > 3}
    return bool(mine & yours)


def match_strength(film, candidate):
    """"strong", "ok", or None — never a score to sort by.

    `country` is deliberately not a signal: Cineteca gets it wrong at the
    source (it files "El caimán humano" / The Alligator People as México).
    """
    ours = {fold(film.get("title")), fold(film.get("original_title"))} - {""}
    theirs = {fold(candidate.get("name")), fold(candidate.get("originalName"))} - {""}
    title_ok = bool(ours & theirs)
    director_ok = directors_agree(film.get("director"), candidate.get("directors"))

    year, their_year = film.get("year"), candidate.get("releaseYear")
    year_ok = bool(year and their_year and abs(year - their_year) <= YEAR_SLACK)

    runtime, their_runtime = film.get("duration_min"), candidate.get("runTime")
    runtime_ok = bool(
        runtime and their_runtime and abs(runtime - their_runtime) <= RUNTIME_SLACK
    )

    # A director match plus any second agreement. This is what carries revivals
    # whose year is the re-release ("Cars 20 aniversario" is filed as 2026) and
    # films whose Spanish title shares nothing with the Letterboxd one.
    if director_ok and (title_ok or year_ok or runtime_ok):
        return "strong"
    # No director to lean on, so demand everything else instead.
    if title_ok and year_ok and runtime_ok:
        return "strong"
    if title_ok and (year_ok or runtime_ok):
        return "ok"
    return None


def pick_match(film, candidates):
    """First strong match, else first weak one, else nothing."""
    fallback = None
    for candidate in candidates:
        strength = match_strength(film, candidate)
        if strength == "strong":
            return candidate
        if strength == "ok" and fallback is None:
            fallback = candidate
    return fallback


def _default_fetch(query):
    """GET the search endpoint. Raises on anything that isn't usable JSON."""
    url = SEARCH_URL + "?" + urllib.parse.urlencode(
        {"input": query, "include": "FilmSearchItem", "perPage": PER_PAGE}
    )
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)
    raise last_exc


def search_films(query, fetch=None):
    """The FilmSummary dicts for a query, best-ranked first."""
    payload = (fetch or _default_fetch)(query)
    items = payload.get("items") or []
    return [
        item["film"]
        for item in items
        if item.get("type") == "FilmSearchItem" and item.get("film")
    ]


def resolve_film(film, fetch=None):
    """`{"url", "rating"}` for a confidently matched, rated film — else None.

    Returns None both when nothing matches and when the match is certain but
    unrated: Letterboxd withholds an average until a film has enough ratings,
    and a badge with no score is not worth the row it sits on.

    Raises whatever the fetch raises. The caller has to tell a clean no-match
    (overwrite the old value) apart from an outage (keep it).
    """
    if is_skippable(film):
        return None

    for query in query_titles(film):
        match = pick_match(film, search_films(query, fetch))
        if match is None:
            continue
        rating, url = match.get("rating"), match.get("link")
        if rating is None or not url:
            return None
        # Rounded to the precision the badge actually shows. The raw average is
        # a long float that drifts on every run, which would rewrite the whole
        # of schedule.json twice a day and cost the one-line-diff property that
        # run()'s canonical sort exists to protect.
        return {"url": url, "rating": round(float(rating), 1)}
    return None
