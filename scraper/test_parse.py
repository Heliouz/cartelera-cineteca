"""Offline tests against saved HTML/JSON fixtures — no network access."""
import json
import os

import pytest

import parsing
import scrape

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def days():
    html = load_fixture("cartelera_page.html")
    return parsing.extract_day_window(html)


@pytest.fixture(scope="module")
def date_lookup(days):
    return parsing.build_date_lookup(days)


@pytest.fixture(scope="module")
def ciclo_map():
    html = json.loads(load_fixture("vista_events.json"))["html"]
    return parsing.extract_ciclo_map(html)


# ---------- day window ----------

def test_extract_day_window_returns_seven_ordered_days(days):
    assert len(days) == 7
    assert days == sorted(days)
    for d in days:
        assert len(d) == 10 and d[4] == "-" and d[7] == "-"


def test_build_date_lookup_maps_day_month_to_iso(days, date_lookup):
    y, m, d = (int(x) for x in days[0].split("-"))
    assert date_lookup[(d, m)] == days[0]


# ---------- film list / ciclo map ----------

def test_extract_film_refs_from_vista_full():
    html = json.loads(load_fixture("vista_full.json"))["html"]
    refs = parsing.extract_film_refs(html)
    assert len(refs) == 68
    ids = {fid for fid, _ in refs}
    assert "HO00009798" in ids


def test_extract_ciclo_map_covers_all_films(ciclo_map):
    full_html = json.loads(load_fixture("vista_full.json"))["html"]
    all_ids = {fid for fid, _ in parsing.extract_film_refs(full_html)}
    assert set(ciclo_map.keys()) == all_ids
    assert ciclo_map["HO00009798"]  # has a non-empty ciclo name


# ---------- showtime parsing / dedup ----------

def test_showtimes_deduplicated_by_session_id(date_lookup):
    html = load_fixture("detail_two_sede.html")
    showtimes = parsing.extract_showtimes(html, date_lookup)
    session_ids = [s["session_id"] for s in showtimes]
    assert len(session_ids) == len(set(session_ids))
    # the raw markup ships each showtime twice (mobile + desktop markup)
    raw_matches = parsing.SHOWTIME_RE.findall(html)
    assert len(raw_matches) == 2 * len(showtimes)


def test_showtime_datetime_has_mexico_city_offset(date_lookup):
    html = load_fixture("detail_one_sede.html")
    showtimes = parsing.extract_showtimes(html, date_lookup)
    assert showtimes
    for s in showtimes:
        assert s["datetime"].endswith("-06:00") or s["datetime"].endswith("-05:00")


def test_showtime_buy_url_matches_session(date_lookup):
    html = load_fixture("detail_one_sede.html")
    showtimes = parsing.extract_showtimes(html, date_lookup)
    for s in showtimes:
        assert s["buy_url"] == (
            "https://rbvfcn.cinetecanacional.net/Ticketing/visSelectTickets.aspx"
            f"?cinemacode={s['sede']}&txtSessionId={s['session_id']}&visLang=1"
        )


# ---------- parenthetical field extraction (format varies — §3.5) ----------

@pytest.mark.parametrize(
    "raw_meta,expected_title,expected_country,expected_year,expected_duration",
    [
        (
            "(Teenage Sex and Death at Camp Miasma, Dir.: Jane Schoenbrun, "
            "Estados Unidos-Canadá, 2026, Dur.: 112 mins.)",
            "Teenage Sex and Death at Camp Miasma",
            "Estados Unidos-Canadá",
            2026,
            112,
        ),
        (
            "(Emergency Exit, 2025, Dur.: 96 mins.)",
            "Emergency Exit",
            None,
            2025,
            96,
        ),
        (
            "(Khmeli potoli, Dir.: Alexandre Koberidze, Georgia-Alemania, Dur.: 186 mins.)",
            "Khmeli potoli",
            "Georgia-Alemania",
            None,
            186,
        ),
        (
            "(El inicio de la animación mexicana, Dir.: X, México, 1935-1938, Dur.: 75 mins.)",
            "El inicio de la animación mexicana",
            "México",
            1935,
            75,
        ),
    ],
)
def test_parse_parenthetical_variants(
    raw_meta, expected_title, expected_country, expected_year, expected_duration
):
    title, country, year, duration = parsing.parse_parenthetical(raw_meta)
    assert title == expected_title
    assert country == expected_country
    assert year == expected_year
    assert duration == expected_duration


def test_parse_parenthetical_handles_none():
    assert parsing.parse_parenthetical(None) == (None, None, None, None)


# ---------- full film detail parsing ----------

@pytest.mark.parametrize(
    "fixture,film_id,cinemas,expected_sedes",
    [
        ("detail_one_sede.html", "HO00009838", "003", {"003"}),
        ("detail_two_sede.html", "HO00009798", "001,002", {"001", "002"}),
        ("detail_three_sede.html", "HO00009793", "001,002,003", {"001", "002", "003"}),
    ],
)
def test_parse_film_detail_html_shape(date_lookup, ciclo_map, fixture, film_id, cinemas, expected_sedes):
    html = load_fixture(fixture)
    film = parsing.parse_film_detail_html(html, film_id, cinemas, date_lookup, ciclo_map.get(film_id))

    for key in ("id", "title", "poster", "official_url", "showtimes"):
        assert film[key], f"missing {key}"
    assert film["id"] == film_id
    assert film["poster"] == parsing.poster_url(film_id)
    assert set(s["sede"] for s in film["showtimes"]) <= expected_sedes
    # no mojibake / replacement chars anywhere in extracted text fields
    for key in ("title", "director", "cast", "country", "synopsis", "raw_meta", "classification"):
        value = film.get(key)
        if value:
            assert "�" not in value


def test_parse_film_detail_html_known_values(date_lookup, ciclo_map):
    html = load_fixture("detail_two_sede.html")
    film = parsing.parse_film_detail_html(html, "HO00009798", "001,002", date_lookup, None)
    assert film["title"] == "Adolescencia, sexo y muerte en Campamento Miasma"
    assert film["director"] == "Jane Schoenbrun"
    assert film["trailer_youtube_id"] == "dimCiC_hdoA"


def test_parse_film_detail_html_accents_survive_roundtrip(date_lookup, ciclo_map):
    """Regression: the raw bytes for some fields once got force-decoded as
    cp1252, splitting UTF-8 accented characters into mojibake pairs."""
    html = load_fixture("detail_three_sede.html")
    film = parsing.parse_film_detail_html(html, "HO00009793", "001,002,003", date_lookup, None)
    assert film["director"] == "Lluís Miñarro"


def test_parse_film_detail_html_raises_on_missing_title(date_lookup):
    corrupted = "<html><body>no title div here</body></html>"
    with pytest.raises(ValueError):
        parsing.parse_film_detail_html(corrupted, "HO0FAKE", "001", date_lookup, None)


# ---------- scraper safety rails ----------

def test_should_abort_for_failures_threshold():
    assert scrape.should_abort_for_failures(100, 21) is True
    assert scrape.should_abort_for_failures(100, 20) is False
    assert scrape.should_abort_for_failures(0, 0) is True


def test_should_abort_for_film_count_threshold():
    assert scrape.should_abort_for_film_count(30, 68) is True
    assert scrape.should_abort_for_film_count(40, 68) is False
    assert scrape.should_abort_for_film_count(5, 0) is False


def test_validate_schedule_rejects_duplicate_session_id():
    data = {
        "days": ["2026-08-21"],
        "sedes": {"001": {}},
        "films": [
            {
                "id": "A",
                "title": "A",
                "poster": "x",
                "official_url": "x",
                "showtimes": [{"session_id": "1"}],
            },
            {
                "id": "B",
                "title": "B",
                "poster": "x",
                "official_url": "x",
                "showtimes": [{"session_id": "1"}],
            },
        ],
    }
    with pytest.raises(AssertionError):
        scrape.validate_schedule(data)


def test_run_aborts_when_failure_ratio_exceeds_threshold(monkeypatch, tmp_path, days, date_lookup):
    """End-to-end: >20% of detail fetches corrupted -> run() must exit(1) and
    must not write a schedule.json. (§11.6)"""
    good_html = load_fixture("detail_one_sede.html")
    corrupted_html = "<html><body>corrupted, no title div</body></html>"

    # 10 "films": 7 corrupted (70% failure, well over the 20% threshold), 3 good
    film_refs = {f"FAKE{i:02d}": "003" for i in range(10)}

    def fake_fetch_detail(film_id, cinemas_csv):
        idx = int(film_id.replace("FAKE", ""))
        return good_html if idx < 3 else corrupted_html

    out_path = tmp_path / "schedule.json"
    monkeypatch.setattr(scrape, "get_day_window", lambda: days)
    monkeypatch.setattr(scrape, "get_film_refs", lambda d: film_refs)
    monkeypatch.setattr(scrape, "get_ciclo_map", lambda: {})
    monkeypatch.setattr(scrape, "fetch_film_detail_html", fake_fetch_detail)
    monkeypatch.setattr(scrape, "load_previous", lambda: None)
    monkeypatch.setattr(scrape, "DATA_PATH", str(out_path))

    with pytest.raises(SystemExit) as exc_info:
        scrape.run()
    assert exc_info.value.code != 0
    assert not out_path.exists()


def test_validate_schedule_accepts_clean_data():
    data = {
        "days": ["2026-08-21"],
        "sedes": {"001": {}},
        "films": [
            {
                "id": "A",
                "title": "A",
                "poster": "x",
                "official_url": "x",
                "showtimes": [{"session_id": "1"}, {"session_id": "2"}],
            },
        ],
    }
    scrape.validate_schedule(data)  # should not raise
