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

from parsing import (
    TZ,
    build_date_lookup,
    extract_ciclo_map,
    extract_day_window,
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
MAX_RETRIES = 4
TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrape")


def fetch_with_retry(method, url, **kwargs):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == MAX_RETRIES - 1:
                log.warning("request failed (%s): %s — giving up", url, exc)
                break
            wait = 2 ** attempt
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
    resp = fetch_with_retry("GET", f"{BASE}/cartelera.php")
    html = _decode_html(resp)
    days = extract_day_window(html)
    if not days:
        raise RuntimeError("no day window found on cartelera.php — page shape may have changed")
    return days


def _post_full(fecha):
    resp = fetch_with_retry(
        "POST",
        f"{BASE}/data/cartelera.php",
        data={"vista": "full", "fecha": fecha, "cinema": "000", "eventId": "000"},
    )
    return extract_film_refs(resp.json()["html"])


def get_film_refs(days):
    """Union of (film_id -> cinemas_csv) across fecha='' and every day in the window."""
    refs = {}
    for film_id, cinemas in _post_full(""):
        refs.setdefault(film_id, cinemas)
    for day in days:
        for film_id, cinemas in _post_full(day):
            refs.setdefault(film_id, cinemas)
        time.sleep(REQUEST_DELAY)
    return refs


def get_ciclo_map():
    resp = fetch_with_retry(
        "POST",
        f"{BASE}/data/cartelera.php",
        data={"vista": "events", "fecha": "", "cinema": "000", "eventId": "000"},
    )
    return extract_ciclo_map(resp.json()["html"])


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
        for st in film["showtimes"]:
            sid = st["session_id"]
            _check(sid not in session_ids, f"duplicate session_id {sid} across dataset")
            session_ids.add(sid)


def scrape_one_film(film_id, cinemas_csv, date_lookup, ciclo_map):
    html = fetch_film_detail_html(film_id, cinemas_csv)
    return parse_film_detail_html(html, film_id, cinemas_csv, date_lookup, ciclo_map.get(film_id))


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

    ciclo_map = get_ciclo_map()
    log.info("ciclo map: %d films tagged across ciclos", len(ciclo_map))

    films = []
    failures = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_id = {
            pool.submit(scrape_one_film, fid, cinemas, date_lookup, ciclo_map): fid
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
