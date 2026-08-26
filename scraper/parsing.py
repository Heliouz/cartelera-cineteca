"""Pure parsing functions for the Cineteca Nacional cartelera scraper.

No network calls here — everything takes HTML/text already fetched and
returns plain data structures. Keeps scrape.py (orchestration + I/O) testable
against saved fixtures.
"""
import re
import unicodedata
from datetime import datetime
from html import unescape as html_unescape
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
TICKET_HREF_RE = re.compile(
    r"visSelectTickets\.aspx\?cinemacode=(\d+)&(?:amp;)?txtSessionId=(\d+)")
TICKET_BASE = "https://rbvfcn.cinetecanacional.net/Ticketing/visSelectTickets.aspx"
DATE_RE = re.compile(r"(\d{1,2})\s+de\s+(\w+)", re.I)
TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*H", re.I)

# Distinctive word from the sede name Cineteca puts in each ticket anchor's own
# confirm() dialog — an independent second source for the cinemacode, inside the
# same element. See _sede_agrees_with_confirm().
SEDE_CONFIRM_KEYWORD = {"001": "CHAPULTEPEC", "002": "ARTES", "003": "MEXICO"}
TRAILER_RE = re.compile(r"youtube\.com/embed/([\w-]+)")
DUR_RE = re.compile(r"Dur\.?:\s*(\d+)\s*mins?", re.I)
YEAR_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*-\s*(?:19|20)?\d{2}\b")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
YEAR_ONLY_SEGMENT_RE = re.compile(r"^(?:19|20)\d{2}(\s*-\s*(?:19|20)?\d{2})?$")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


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


def clean_html_text(raw):
    """Plain text out of a raw HTML fragment: tags dropped, entities decoded.

    Ciclo names are captured straight out of the markup by CICLO_SPLIT_RE, so
    anything Cineteca puts inside that heading — an `&amp;`, a stray `<br>` —
    would otherwise reach the page verbatim: these strings go into `<option>`
    labels and kickers via textContent, which renders them literally.
    """
    if raw is None:
        return None
    text = html_unescape(TAG_RE.sub(" ", raw))
    return WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def extract_ciclo_map(html):
    """film_id -> ciclo name, from a `vista=events` HTML fragment."""
    parts = CICLO_SPLIT_RE.split(html)
    mapping = {}
    for i in range(1, len(parts), 2):
        ciclo_name = clean_html_text(parts[i])
        block = parts[i + 1] if i + 1 < len(parts) else ""
        for film_id, _cinemas in FILM_REF_RE.findall(block):
            mapping.setdefault(film_id, ciclo_name)
    return mapping


def _strip_accents(text):
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


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


def _norm_for_match(text):
    return WS_RE.sub(" ", _strip_accents(text or "").replace("\xa0", " ")).strip().lower()


def _title_segment_span(segments, title):
    """How many leading comma-separated segments belong to the original title.

    The parenthetical is comma-delimited, but an original title can contain a
    comma of its own ("Oh, fortuna" / "Frankie, Maniac Woman") — splitting
    blindly leaves the tail of the title sitting in `country`, where it shows
    up on every row and card. The display title is an independent witness for
    the same string, so keep absorbing leading segments while the joined
    result still reads as a prefix of it.

    A prefix, not an equality, because Cineteca truncates long titles. When
    nothing matches — the parenthetical is in another language, or isn't a
    title at all — this falls back to one segment, the original behaviour.
    """
    if not title or not segments:
        return 1
    norm_title = _norm_for_match(title)
    if not norm_title:
        return 1
    span = 1
    for count in range(1, len(segments) + 1):
        joined = _norm_for_match(", ".join(segments[:count]))
        if not joined or not norm_title.startswith(joined):
            break
        span = count
    return span


def parse_parenthetical(raw_meta, title=None):
    """original_title, country, year, duration_min — field-by-field, all nullable.

    `title` is the film's display title, used only to keep a comma inside the
    original title from spilling into `country`. See _title_segment_span().
    """
    if not raw_meta:
        return None, None, None, None
    inner = raw_meta.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    segments = [s.strip() for s in inner.split(",")]

    span = _title_segment_span(segments, title)
    original_title = ", ".join(segments[:span]).strip() or None
    rest = segments[span:]

    dur_m = DUR_RE.search(inner)
    duration_min = int(dur_m.group(1)) if dur_m else None

    # Year is looked for only past the title, so a title carrying a year of its
    # own ("Blade Runner 2049") can't be mistaken for the production year.
    rest_text = ", ".join(rest)
    rest_dur_m = DUR_RE.search(rest_text)
    search_region = rest_text[: rest_dur_m.start()] if rest_dur_m else rest_text

    range_m = YEAR_RANGE_RE.search(search_region)
    if range_m:
        year = int(range_m.group(1))
    else:
        year_matches = YEAR_RE.findall(search_region)
        year = int(year_matches[-1]) if year_matches else None

    filtered = []
    for seg in rest:
        if not seg:
            continue
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


def _sede_agrees_with_confirm(cinemacode, onclick):
    """Does the anchor's own confirm() dialog name the sede its cinemacode means?

    Cineteca writes the sede out in words in the onclick handler, so a showtime
    row carries the sede twice. Disagreement means we misread the row — drop it
    rather than publish a screening at the wrong sede. Silence (no dialog, or
    wording we don't recognise) isn't disagreement: the cinemacode stands alone.
    """
    if not onclick:
        return True
    text = _strip_accents(onclick).upper()
    present = {code for code, kw in SEDE_CONFIRM_KEYWORD.items() if kw in text}
    if not present:
        return True
    return present == {cinemacode}


def extract_showtimes(html, date_lookup):
    """Deduplicated showtimes (by session_id) with resolved ISO dates/datetimes.

    Parsed one `<a>` at a time on purpose: an anchor whose label doesn't carry a
    readable date and time is skipped alone. A regex spanning href-to-label used
    to run past such a row into the next one, pairing a session's cinemacode
    with the following session's time and dropping that row entirely. That rule
    is what makes buy_url trustworthy: the checkout link and the date, time and
    sede printed beside it all come from the same element or none do.
    """
    soup = BeautifulSoup(html, "html.parser")
    by_session = {}
    for a in soup.find_all("a", href=True):
        href_m = TICKET_HREF_RE.search(a["href"])
        if not href_m:
            continue
        cinemacode, session_id = href_m.groups()
        if session_id in by_session:
            continue
        if not _sede_agrees_with_confirm(cinemacode, a.get("onclick")):
            continue
        label = a.get_text(" ", strip=True)
        time_m = TIME_RE.search(label)
        if not time_m:
            continue
        time_text = time_m.group(1)
        day_m = DATE_RE.search(label[: time_m.start()])
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
            "buy_url": build_buy_url(cinemacode, session_id),
        }
    return list(by_session.values())


def build_buy_url(cinemacode, session_id):
    """Vista's checkout deep link for one screening.

    Not constructed so much as copied: both halves are read out of the same
    single `<a href>` on detallePelicula.php, so this cannot address a session
    that Cineteca didn't publish. Two things keep it pointed at the right one —
    extract_showtimes() never lets a row's href and label come from different
    anchors, and Vista namespaces session ids per cinema, so an id paired with
    the wrong cinemacode resolves to nothing rather than to someone else's
    screening.
    """
    return f"{TICKET_BASE}?cinemacode={cinemacode}&txtSessionId={session_id}&visLang=1"


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
    original_title, country, year, duration_min = parse_parenthetical(raw_meta, title)
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
