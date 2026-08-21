"""Pure parsing functions for the Cineteca Nacional cartelera scraper.

No network calls here — everything takes HTML/text already fetched and
returns plain data structures. Keeps scrape.py (orchestration + I/O) testable
against saved fixtures.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Mexico_City")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}

DAY_RE = re.compile(r"cartelera\.php\?dia=(\d{4}-\d{2}-\d{2})")
FILM_REF_RE = re.compile(r"detallePelicula\.php\?FilmId=(\w+)&cinemas=([\d,]+)")
CICLO_SPLIT_RE = re.compile(r'<p class="font-weight-bold text-uppercase h3 py-5">(.*?)</p>')
SHOWTIME_RE = re.compile(
    r"cinemacode=(\d+)&amp;txtSessionId=(\d+)&amp;visLang=1'"
    r'.*?small">([^<]+)<br>\s*(\d{1,2}:\d{2})\s*H', re.S)
DATE_RE = re.compile(r"(\d{1,2})\s+de\s+(\w+)", re.I)
TRAILER_RE = re.compile(r"youtube\.com/embed/([\w-]+)")
DUR_RE = re.compile(r"Dur\.?:\s*(\d+)\s*mins?", re.I)
YEAR_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*-\s*(?:19|20)?\d{2}\b")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
YEAR_ONLY_SEGMENT_RE = re.compile(r"^(?:19|20)\d{2}(\s*-\s*(?:19|20)?\d{2})?$")


def extract_day_window(html):
    """Ordered, deduplicated list of ISO date strings from cartelera.php's day picker."""
    days = []
    for d in DAY_RE.findall(html):
        if d not in days:
            days.append(d)
    return days


def build_date_lookup(days):
    """(day_number, month_number) -> ISO date, from the authoritative day window."""
    lookup = {}
    for d in days:
        y, m, dd = (int(x) for x in d.split("-"))
        lookup[(dd, m)] = d
    return lookup


def extract_film_refs(html):
    """List of (film_id, cinemas_csv) from a `vista=full` HTML fragment."""
    return FILM_REF_RE.findall(html)


def extract_ciclo_map(html):
    """film_id -> ciclo name, from a `vista=events` HTML fragment."""
    parts = CICLO_SPLIT_RE.split(html)
    mapping = {}
    for i in range(1, len(parts), 2):
        ciclo_name = parts[i].strip()
        block = parts[i + 1] if i + 1 < len(parts) else ""
        for film_id, _cinemas in FILM_REF_RE.findall(block):
            mapping.setdefault(film_id, ciclo_name)
    return mapping


def _clean(text):
    if text is None:
        return None
    t = text.replace("\xa0", " ").strip()
    t = t.rstrip(".").strip()
    return t or None


_LABEL_MAP = {
    "director": "director",
    "guion": "screenplay",
    "guión": "screenplay",
    "con": "cast",
    "clasificacion": "classification",
    "clasificación": "classification",
}


def _extract_metadata_fields(soup):
    """Reads the labelled `Director:` / `Con:` / `Clasificación:` ... spans."""
    fields = {}
    for span in soup.find_all("span", class_="font-weight-bold"):
        label_raw = span.get_text(strip=True).rstrip(":").strip().lower()
        label = _LABEL_MAP.get(label_raw)
        if not label:
            continue
        sib = span.next_sibling
        value = sib if isinstance(sib, str) else ""
        value = _clean(value)
        if label not in fields:
            fields[label] = value
    return fields


def _extract_synopsis(soup):
    for p in soup.find_all("p"):
        if p.get("class") == ["lh-1", "text-justify"]:
            text = p.get_text(strip=True)
            if text:
                return text
    return None


def _extract_raw_meta(soup):
    for p in soup.find_all("p"):
        if p.get("class") == ["lh-1"]:
            text = p.get_text(strip=True)
            if text.startswith("("):
                return text
    return None


def parse_parenthetical(raw_meta):
    """original_title, country, year, duration_min — field-by-field, all nullable."""
    if not raw_meta:
        return None, None, None, None
    inner = raw_meta.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    segments = [s.strip() for s in inner.split(",")]
    original_title = segments[0] or None if segments else None

    dur_m = DUR_RE.search(inner)
    duration_min = int(dur_m.group(1)) if dur_m else None
    search_region = inner[: dur_m.start()] if dur_m else inner

    range_m = YEAR_RANGE_RE.search(search_region)
    if range_m:
        year = int(range_m.group(1))
    else:
        year_matches = YEAR_RE.findall(search_region)
        year = int(year_matches[-1]) if year_matches else None

    filtered = []
    for seg in segments[1:]:
        low = seg.lower()
        if low.startswith("dir."):
            continue
        if "dur." in low or low.endswith("mins") or low.endswith("mins."):
            continue
        if YEAR_ONLY_SEGMENT_RE.match(seg):
            continue
        filtered.append(seg)
    country = ", ".join(filtered) if filtered else None

    return original_title, country, year, duration_min


def extract_showtimes(html, date_lookup):
    """Deduplicated showtimes (by session_id) with resolved ISO dates/datetimes."""
    matches = SHOWTIME_RE.findall(html)
    by_session = {}
    for cinemacode, session_id, date_text, time_text in matches:
        if session_id in by_session:
            continue
        day_m = DATE_RE.search(date_text.strip())
        if not day_m:
            continue
        day_num = int(day_m.group(1))
        month_num = MESES.get(day_m.group(2).lower())
        if month_num is None:
            continue
        iso_date = date_lookup.get((day_num, month_num))
        if iso_date is None:
            continue  # outside the known schedule horizon — shouldn't happen (§3.5)
        hh, mm = (int(x) for x in time_text.split(":"))
        y, m, d = (int(x) for x in iso_date.split("-"))
        dt = datetime(y, m, d, hh, mm, tzinfo=TZ)
        by_session[session_id] = {
            "sede": cinemacode,
            "date": iso_date,
            "time": time_text,
            "datetime": dt.isoformat(),
            "session_id": session_id,
            "buy_url": (
                "https://rbvfcn.cinetecanacional.net/Ticketing/visSelectTickets.aspx"
                f"?cinemacode={cinemacode}&txtSessionId={session_id}&visLang=1"
            ),
        }
    return list(by_session.values())


def poster_url(film_id):
    return (
        "https://rbvfcn.cinetecanacional.net/CDN/media/entity/get/FilmPosterGraphic/"
        f"{film_id}?referenceScheme=Cinema&allowPlaceHolder"
    )


def parse_film_detail_html(html, film_id, cinemas_csv, date_lookup, ciclo=None):
    """Full film record for the schedule.json contract, or raises on unrecoverable data."""
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("div.font-weight-bold.text-uppercase.h3")
    if title_el is None:
        raise ValueError(f"{film_id}: no title element found")
    title = title_el.get_text(strip=True)

    meta = _extract_metadata_fields(soup)
    raw_meta = _extract_raw_meta(soup)
    original_title, country, year, duration_min = parse_parenthetical(raw_meta)
    synopsis = _extract_synopsis(soup)

    trailer_m = TRAILER_RE.search(html)
    trailer_id = trailer_m.group(1) if trailer_m else None

    showtimes = extract_showtimes(html, date_lookup)

    return {
        "id": film_id,
        "title": title,
        "original_title": original_title,
        "director": meta.get("director"),
        "cast": meta.get("cast"),
        "country": country,
        "year": year,
        "duration_min": duration_min,
        "classification": meta.get("classification"),
        "synopsis": synopsis,
        "raw_meta": raw_meta,
        "poster": poster_url(film_id),
        "trailer_youtube_id": trailer_id,
        "ciclo": ciclo,
        "official_url": (
            f"https://www.cinetecanacional.net/detallePelicula.php"
            f"?FilmId={film_id}&cinemas={cinemas_csv}"
        ),
        "showtimes": showtimes,
    }
