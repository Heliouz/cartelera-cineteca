#!/usr/bin/env python3
"""Scrapes cinetecanacional.net's cartelera into docs/data/schedule.json.

Run twice a day by .github/workflows/scrape.yml. Safety rails (abort
thresholds, atomic write, duplicate session_id check) matter more than
anything else here — a silently-broken scrape must never overwrite good data
with an empty or partial file. Output is sorted so that an unchanged cartelera
produces a one-line diff (the `generated_at` timestamp) rather than a
whole-file reshuffle.
"""
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

import letterboxd
from parsing import (
    TZ,
    build_buy_url,
    build_date_lookup,
    build_day_window,
    extract_film_refs,
    parse_film_detail_html,
)

BASE = "https://www.cinetecanacional.net"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "CarteleraCinetecaBot/1.0 (+https://github.com/heliouz/cartelera-cineteca)"
)
HEADERS = {"User-Agent": USER_AGENT}

SEDES = {
    "003": {
        "name": "México",
        "full_name": "Cineteca Nacional México",
        "address": "Av. México Coyoacán 389, Xoco, Benito Juárez, CDMX",
        "color": "#EB1C23",
    },
    "001": {
        "name": "Chapultepec",
        "full_name": "Cineteca Nacional Chapultepec",
        "address": "Av. Vasco de Quiroga 1401, Santa Fe, CDMX",
        "color": "#28724F",
    },
    "002": {
        "name": "Las Artes",
        "full_name": "Cineteca Nacional de las Artes",
        "address": "Av. Río Churubusco 79, Coyoacán, CDMX",
        "color": "#653090",
    },
}

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "schedule.json")
MAX_WORKERS = 5
REQUEST_DELAY = 0.15
FAIL_ABORT_RATIO = 0.2
FILM_COUNT_MIN_RATIO = 0.5
MAX_RETRIES = 5
TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrape")


def fetch_with_retry(method, url, parse=None, **kwargs):
    """Fetch a URL, retrying the transient failures cinetecanacional.net serves.

    `parse` runs *inside* the loop and its result is what comes back, so a
    response that arrives whole but unreadable retries exactly like a 500 does.
    That placement is the whole point: `resp.json()` used to sit out at the call
    sites, and on 2026-09-03 one 200 whose body stopped 11 bytes in ended the
    run with no retry at all — while the 500s a day earlier at least got four.

    Backoff is 2/4/8/16s over 5 attempts, ~30s of cover. The old 1/2/4 over 4
    attempts spent itself in 7s, short enough that a single ~11s outage
    upstream took the whole run down.
    """
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return parse(resp) if parse else resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == MAX_RETRIES - 1:
                log.warning("request failed (%s): %s — giving up", url, exc)
                break
            wait = 2 ** (attempt + 1)
            log.warning("request failed (%s): %s — retrying in %ss", url, exc, wait)
            time.sleep(wait)
    raise last_exc


def _decode_html(resp):
    """Decode a page's body as UTF-8.

    The site declares (and, verified live, actually serves) UTF-8 for these
    pages — despite an earlier version of this scraper's spec assuming
    cp1252, which produced mojibake on every accented name. errors="replace"
    is a last-resort guard, not the expected path: log loudly if it's ever
    hit, since it would mean the site's encoding changed again.
    """
    html = resp.content.decode("utf-8", errors="replace")
    if "�" in html:
        log.warning("replacement chars after utf-8 decode of %s — encoding may have changed", resp.url)
    return html


def get_day_window():
    """The seven days this run covers, from today's date in CDMX.

    No longer fetched. cartelera.php served the window as a picker of
    `?dia=<ISO>` links until Cineteca rewrote it as a client-rendered page in
    September 2026, after which the HTML carries no dates at all and this
    raised "page shape may have changed" on every run. See build_day_window()
    for why arithmetic is a faithful replacement rather than a guess.
    """
    return build_day_window(datetime.now(TZ).date())


def _listing_films(resp):
    """Pull the `data` array out of an obtener_cartelera.php response.

    Both failure modes raise a `requests.RequestException` subclass on purpose
    (`JSONDecodeError` already is one), so `fetch_with_retry` retries them
    without having to widen what it catches.
    """
    payload = resp.json()
    status = payload.get("status")
    if status != "success":
        raise requests.exceptions.InvalidJSONError(
            f"status={status!r} in the response from {resp.url}", response=resp
        )
    films = payload.get("data")
    if not isinstance(films, list):
        raise requests.exceptions.InvalidJSONError(
            f"no 'data' array in the response from {resp.url}", response=resp
        )
    return films


def _get_listing(fecha):
    """Film refs for one date. An empty `fecha` means "everything on sale"."""
    films = fetch_with_retry(
        "GET",
        f"{BASE}/obtener_cartelera.php",
        parse=_listing_films,
        params={"busqueda": "", "fecha": fecha, "sede": ""},
    )
    return extract_film_refs(films)


def get_film_refs(days):
    """Union of film_id -> cinemas_csv across fecha='' and every day in the window.

    The sede codes are unioned across days rather than taken from whichever
    listing mentioned a film first: a film can open at one sede midweek and
    another at the weekend, and cinemas_csv is what official_url sends a
    visitor to. (detallePelicula.php itself ignores the parameter and returns
    every sede's showtimes regardless, so this cannot cost us a screening —
    it only keeps the link we publish honest.)
    """
    sedes_by_film = {}
    for fecha in [""] + list(days):
        for film_id, cinemas_csv in _get_listing(fecha):
            bucket = sedes_by_film.setdefault(film_id, set())
            bucket.update(c for c in cinemas_csv.split(",") if c)
        time.sleep(REQUEST_DELAY)
    return {fid: ",".join(sorted(sedes)) for fid, sedes in sedes_by_film.items()}


def fetch_film_detail_html(film_id, cinemas_csv):
    resp = fetch_with_retry(
        "GET", f"{BASE}/detallePelicula.php", params={"FilmId": film_id, "cinemas": cinemas_csv}
    )
    return _decode_html(resp)


def load_previous():
    if not os.path.exists(DATA_PATH):
        return None
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read previous schedule.json: %s", exc)
        return None


def should_abort_for_failures(total, failures):
    if total == 0:
        return True
    return (failures / total) > FAIL_ABORT_RATIO


def should_abort_for_film_count(current_count, previous_count):
    if not previous_count:
        return False
    return current_count < previous_count * FILM_COUNT_MIN_RATIO


class ScheduleInvalid(Exception):
    """The assembled schedule failed its own contract — never write it."""


def _check(condition, message):
    # Not `assert`: these are the last gate before overwriting live data, and
    # `python -O` strips assert statements.
    if not condition:
        raise ScheduleInvalid(message)


def validate_schedule(data):
    _check(isinstance(data.get("days"), list) and data["days"], "empty day window")
    _check(isinstance(data.get("sedes"), dict) and data["sedes"], "empty sedes")
    films = data.get("films")
    _check(isinstance(films, list) and films, "empty films list")

    session_ids = set()
    for film in films:
        for key in ("id", "title", "poster", "official_url", "showtimes"):
            _check(key in film, f"film {film.get('id')} missing required key {key}")
        # Optional, so an older cached schedule.json still validates. When it is
        # there it has to address a Letterboxd film page and carry a score the
        # badge can actually draw.
        lb = film.get("letterboxd")
        if lb is not None:
            _check(
                str(lb.get("url", "")).startswith("https://letterboxd.com/film/"),
                f"film {film.get('id')}: letterboxd url is not a letterboxd film page",
            )
            _check(
                isinstance(lb.get("rating"), (int, float))
                and not isinstance(lb.get("rating"), bool)
                and 0 <= lb["rating"] <= 5,
                f"film {film.get('id')}: letterboxd rating out of range",
            )
        for st in film["showtimes"]:
            sid = st["session_id"]
            _check(sid not in session_ids, f"duplicate session_id {sid} across dataset")
            session_ids.add(sid)
            buy_url = st.get("buy_url")
            # Cheap, but it's the last gate: a link that doesn't address its own
            # row's sede and session is the one bug that could sell a stranger
            # the wrong ticket.
            _check(
                buy_url is None or buy_url == build_buy_url(st["sede"], sid),
                f"session {sid}: buy_url does not address its own sede and session",
            )


def scrape_one_film(film_id, cinemas_csv, date_lookup):
    """One film's record. `ciclo` stays None — nothing publishes it any more.

    Ciclo names came from the grouping headings on /data/cartelera.php's
    `vista=events` fragment, which went dark with the rest of that endpoint in
    September 2026; obtener_cartelera.php has no equivalent field and no other
    page on the site exposes one. The schedule.json key and the frontend's
    filter are left in place, so if Cineteca ever publishes ciclos again this
    is the one line that has to change.
    """
    html = fetch_film_detail_html(film_id, cinemas_csv)
    return parse_film_detail_html(html, film_id, cinemas_csv, date_lookup)


def attach_letterboxd(films, previous):
    """Set film["letterboxd"] on every film. Never raises.

    Letterboxd is a nice-to-have bolted onto a cartelera that has to keep
    working without it, so nothing in here may abort the run or count toward
    `should_abort_for_failures` — that ratio guards Cineteca detail fetches,
    which are the data the site is actually for.

    A film that resolves to nothing gets None even if it had a value before:
    a mismatch corrected upstream has to be able to clear itself. A film whose
    *lookup failed* keeps its previous value instead, so a Letterboxd outage
    doesn't blank every badge on the site at once.
    """
    previous_by_id = {
        f["id"]: f.get("letterboxd") for f in (previous or {}).get("films", [])
    }
    matched = carried = 0

    def resolve(film):
        return film["id"], letterboxd.resolve_film(film)

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = {}
            futures = {pool.submit(resolve, f): f["id"] for f in films}
            for future in as_completed(futures):
                film_id = futures[future]
                try:
                    _, resolved = future.result()
                    results[film_id] = resolved
                except Exception as exc:  # noqa: BLE001 — optional data, never fatal
                    log.warning("letterboxd lookup failed for %s: %s", film_id, exc)
                    results[film_id] = previous_by_id.get(film_id)
                    carried += 1
        for film in films:
            film["letterboxd"] = results.get(film["id"])
            if film["letterboxd"]:
                matched += 1
    except Exception as exc:  # noqa: BLE001 — a broken pass must still ship a cartelera
        log.warning("letterboxd pass failed wholesale: %s", exc)
        for film in films:
            film.setdefault("letterboxd", previous_by_id.get(film["id"]))
        return

    log.info(
        "letterboxd: %d rated, %d without a badge, %d carried over from last run",
        matched,
        len(films) - matched,
        carried,
    )


def run():
    log.info("starting scrape")

    days = get_day_window()
    log.info("day window (%d days): %s", len(days), days)
    date_lookup = build_date_lookup(days)

    film_refs = get_film_refs(days)
    log.info("film refs collected: %d", len(film_refs))
    if not film_refs:
        log.error("abort: no films found in film list endpoint")
        sys.exit(1)

    films = []
    failures = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_id = {
            pool.submit(scrape_one_film, fid, cinemas, date_lookup): fid
            for fid, cinemas in film_refs.items()
        }
        for future in as_completed(future_to_id):
            fid = future_to_id[future]
            try:
                films.append(future.result())
            except Exception as exc:  # noqa: BLE001 — per-film isolation is the point
                log.error("film %s failed to parse: %s", fid, exc)
                failures.append(fid)

    fail_ratio = len(failures) / len(film_refs)
    log.info(
        "parsed %d films, %d failures (%.1f%%)", len(films), len(failures), fail_ratio * 100
    )
    if should_abort_for_failures(len(film_refs), len(failures)):
        log.error(
            "abort: failure ratio %.1f%% exceeds %.0f%% threshold",
            fail_ratio * 100,
            FAIL_ABORT_RATIO * 100,
        )
        sys.exit(1)

    previous = load_previous()
    if previous is not None:
        prev_count = len(previous.get("films", []))
        if should_abort_for_film_count(len(films), prev_count):
            log.error(
                "abort: film count %d is below %.0f%% of previous run's %d",
                len(films),
                FILM_COUNT_MIN_RATIO * 100,
                prev_count,
            )
            sys.exit(1)

    attach_letterboxd(films, previous)

    # Deterministic order. Films arrive in thread-completion order, so without
    # this an unchanged cartelera still rewrites all ~200KB in a different
    # arrangement. `generated_at` moves every run either way — that's deliberate,
    # the frontend's "actualizado hace N h" and stale banner read it — so the
    # point here isn't to avoid the commit, it's to keep that commit a
    # one-line timestamp diff instead of a whole-file reshuffle.
    films.sort(key=lambda f: f["id"])
    for film in films:
        film["showtimes"].sort(key=lambda st: (st["datetime"], st["sede"], st["session_id"]))

    data = {
        "generated_at": datetime.now(TZ).isoformat(),
        "source": f"{BASE}/",
        "days": days,
        "sedes": SEDES,
        "films": films,
    }

    try:
        validate_schedule(data)
    except ScheduleInvalid as exc:
        log.error("abort: validation failed: %s", exc)
        sys.exit(1)

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    tmp_path = DATA_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with open(tmp_path, "r", encoding="utf-8") as f:
        json.load(f)  # round-trip check before it goes live

    os.replace(tmp_path, DATA_PATH)

    total_showtimes = sum(len(f["showtimes"]) for f in films)
    log.info(
        "done: %d films, %d showtimes, %d days covered, %d failures -> %s",
        len(films),
        total_showtimes,
        len(days),
        len(failures),
        DATA_PATH,
    )


if __name__ == "__main__":
    run()
