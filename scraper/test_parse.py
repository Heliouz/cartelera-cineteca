"""Offline tests against saved HTML/JSON fixtures — no network access."""
import json
import os
import sys

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
    raw_matches = parsing.TICKET_HREF_RE.findall(html)
    assert len(raw_matches) == 2 * len(showtimes)


def _ticket_anchor(cinemacode, session_id, sede_name, label):
    return (
        "<a href='https://rbvfcn.cinetecanacional.net/Ticketing/visSelectTickets.aspx"
        f"?cinemacode={cinemacode}&amp;txtSessionId={session_id}&amp;visLang=1'"
        f' onclick="return confirm(\'Estás a punto de comprar entradas para {sede_name}.\')">'
        f'<div class="small">{label}</div></a>'
    )


def test_unreadable_showtime_does_not_shift_onto_the_next(date_lookup):
    """An anchor with no readable time must be dropped alone, not absorb its
    neighbour's time (which would publish a screening at the wrong sede/hour)."""
    html = (
        _ticket_anchor("001", "13835", "CINETECA NACIONAL CHAPULTEPEC",
                       "Jueves 27 de Agosto AGOTADO")
        + _ticket_anchor("002", "51008", "CINETECA NACIONAL DE LAS ARTES",
                         "Jueves 27 de Agosto <br> 19:00 H")
    )
    showtimes = parsing.extract_showtimes(html, date_lookup)
    assert len(showtimes) == 1
    (s,) = showtimes
    assert s["session_id"] == "51008"
    assert s["sede"] == "002"
    assert s["time"] == "19:00"


def test_showtime_dropped_when_confirm_dialog_contradicts_cinemacode(date_lookup):
    """The sede is in the row twice — cinemacode and Cineteca's own confirm()
    text. If they disagree we misread the row, so publish neither reading."""
    html = _ticket_anchor("001", "13835", "CINETECA NACIONAL DE LAS ARTES",
                          "Jueves 27 de Agosto <br> 19:00 H")
    assert parsing.extract_showtimes(html, date_lookup) == []


def test_showtime_kept_when_confirm_dialog_is_absent_or_unrecognised(date_lookup):
    """Silence isn't disagreement: an anchor with no usable dialog still counts."""
    plain = (
        "<a href='https://rbvfcn.cinetecanacional.net/Ticketing/visSelectTickets.aspx"
        "?cinemacode=001&amp;txtSessionId=13835&amp;visLang=1'>"
        '<div class="small">Jueves 27 de Agosto <br> 19:00 H</div></a>'
    )
    reworded = _ticket_anchor("001", "13836", "NUESTRA NUEVA SALA",
                              "Jueves 27 de Agosto <br> 20:00 H")
    showtimes = parsing.extract_showtimes(plain + reworded, date_lookup)
    assert {s["session_id"] for s in showtimes} == {"13835", "13836"}
    assert all(s["sede"] == "001" for s in showtimes)


def test_showtime_datetime_has_mexico_city_offset(date_lookup):
    html = load_fixture("detail_one_sede.html")
    showtimes = parsing.extract_showtimes(html, date_lookup)
    assert showtimes
    for s in showtimes:
        assert s["datetime"].endswith("-06:00") or s["datetime"].endswith("-05:00")


def test_showtime_has_no_buy_url(date_lookup):
    html = load_fixture("detail_one_sede.html")
    showtimes = parsing.extract_showtimes(html, date_lookup)
    assert showtimes
    for s in showtimes:
        assert set(s.keys()) == {"sede", "date", "time", "datetime", "session_id"}


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


@pytest.mark.parametrize(
    "raw_meta,title,expected_original,expected_country",
    [
        # A comma inside the original title used to push its own tail into
        # `country`, which shows on every row and card ("un lémur en fuga,
        # México"). The display title settles where the title actually ends.
        (
            "(Bem, un lémur en fuga, México, 2026, Dur.: 90 mins.)",
            "Bem, un lémur en fuga",
            "Bem, un lémur en fuga",
            "México",
        ),
        (
            "(Oh, fortuna, México, 2025, Dur.: 95 mins.)",
            "Oh, fortuna",
            "Oh, fortuna",
            "México",
        ),
        (
            "(Frankie, Maniac Woman, Estados Unidos, 2025, Dur.: 95 mins.)",
            "Frankie, Maniac Woman",
            "Frankie, Maniac Woman",
            "Estados Unidos",
        ),
        # No relation between the two titles (original is in another language):
        # falls back to one segment, exactly as before.
        (
            "(Teenage Sex and Death at Camp Miasma, Estados Unidos-Canadá, 2026, "
            "Dur.: 112 mins.)",
            "Adolescencia, sexo y muerte en Campamento Miasma",
            "Teenage Sex and Death at Camp Miasma",
            "Estados Unidos-Canadá",
        ),
        # Empty segments from doubled commas must not survive into `country`
        # as a leading ", ".
        (
            "(Contratos/ El rey de los vagabundos, , México y Bélgica, 2025, "
            "Dur.: 97 mins.)",
            "El rey de los vagabundos",
            "Contratos/ El rey de los vagabundos",
            "México y Bélgica",
        ),
    ],
)
def test_parse_parenthetical_keeps_comma_bearing_title_out_of_country(
    raw_meta, title, expected_original, expected_country
):
    original_title, country, _year, _duration = parsing.parse_parenthetical(raw_meta, title)
    assert original_title == expected_original
    assert country == expected_country


def test_parse_parenthetical_ignores_a_year_inside_the_title():
    """The production year is read past the title, so a title carrying a year
    of its own can't be mistaken for it."""
    _t, _c, year, _d = parsing.parse_parenthetical(
        "(Blade Runner 2049, Estados Unidos, 2017, Dur.: 164 mins.)", "Blade Runner 2049"
    )
    assert year == 2017


def test_clean_html_text_strips_tags_and_entities():
    """Ciclo names are sliced out of raw markup and rendered via textContent,
    so tags and entities have to be resolved here or they reach the page."""
    assert parsing.clean_html_text("Cine &amp; Video<br>2026") == "Cine & Video 2026"
    assert parsing.clean_html_text("  Foro\xa0 Internacional  ") == "Foro Internacional"
    assert parsing.clean_html_text(None) is None


def test_extract_ciclo_map_cleans_names():
    html = (
        '<p class="font-weight-bold text-uppercase h3 py-5">Ciclo &amp; Muestra<br></p>'
        "<a href='detallePelicula.php?FilmId=HO0001&cinemas=003'>x</a>"
    )
    assert parsing.extract_ciclo_map(html) == {"HO0001": "Ciclo & Muestra"}


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
    with pytest.raises(scrape.ScheduleInvalid):
        scrape.validate_schedule(data)


def test_validate_schedule_survives_optimised_mode():
    """The last gate before overwriting live data can't be an `assert`, which
    `python -O` strips out entirely."""
    import subprocess

    src = (
        "import sys; sys.path.insert(0, %r); import scrape;"
        "scrape.validate_schedule({'days': [], 'sedes': {}, 'films': []})"
        % os.path.dirname(os.path.abspath(__file__))
    )
    proc = subprocess.run([sys.executable, "-O", "-c", src], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "ScheduleInvalid" in proc.stderr


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


def test_run_writes_a_canonically_ordered_file(monkeypatch, tmp_path, days):
    """Films arrive in thread-completion order, which varies run to run. The
    written file has to be sorted, or an unchanged cartelera still rewrites all
    ~200KB in a fresh arrangement twice a day."""
    detail_html = load_fixture("detail_two_sede.html")
    film_refs = {f"HO0000{i}": "001,002" for i in range(6)}
    out_path = tmp_path / "schedule.json"

    monkeypatch.setattr(scrape, "get_day_window", lambda: days)
    monkeypatch.setattr(scrape, "get_film_refs", lambda d: film_refs)
    monkeypatch.setattr(scrape, "get_ciclo_map", lambda: {})
    monkeypatch.setattr(scrape, "fetch_film_detail_html", lambda fid, c: detail_html)
    monkeypatch.setattr(scrape, "load_previous", lambda: None)
    monkeypatch.setattr(scrape, "DATA_PATH", str(out_path))
    # Every fake film reuses one fixture, so they share session ids — that trips
    # the duplicate check, which isn't what's under test here.
    monkeypatch.setattr(scrape, "validate_schedule", lambda data: None)

    scrape.run()

    with open(out_path, encoding="utf-8") as f:
        data = json.load(f)

    ids = [f["id"] for f in data["films"]]
    assert ids == sorted(ids)
    for film in data["films"]:
        stamps = [st["datetime"] for st in film["showtimes"]]
        assert stamps == sorted(stamps)


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
