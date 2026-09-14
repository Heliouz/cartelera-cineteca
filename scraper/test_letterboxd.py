"""Offline tests for the Letterboxd matcher — no network access.

The candidate dicts below are trimmed real responses from
api.letterboxd.com/api/v0/search, and every case is one the matcher was
measured against on 2026-09-14. The rejections matter more than the matches:
a wrong rating shown confidently is the only outcome here that is worse than
showing nothing.
"""
import pytest

import letterboxd


def film(**overrides):
    base = {
        "id": "HO00000001",
        "title": "T",
        "original_title": None,
        "director": None,
        "country": None,
        "year": None,
        "duration_min": None,
    }
    base.update(overrides)
    return base


def candidate(name, year=None, runtime=None, directors=(), original=None, rating=3.5):
    return {
        "name": name,
        "originalName": original,
        "releaseYear": year,
        "runTime": runtime,
        "directors": [{"name": d} for d in directors],
        "rating": rating,
        "link": "https://letterboxd.com/film/%s/" % name.lower().replace(" ", "-"),
    }


def fetch_returning(*pages):
    """A fake fetch that replays one response per successive query."""
    remaining = list(pages)

    def fetch(_query):
        page = remaining.pop(0) if remaining else []
        return {"items": [{"type": "FilmSearchItem", "film": c} for c in page]}

    return fetch


# ---------- query construction ----------

@pytest.mark.parametrize(
    "title,expected",
    [
        ("Cars 20 aniversario DOB", "Cars 20 aniversario"),
        ("Coyote Vs Acme SUB", "Coyote Vs Acme"),
        ("Ponyo y el secreto de la sirenita DOB", "Ponyo y el secreto de la sirenita"),
        ("Subterráneo", "Subterráneo"),  # SUB is a suffix, not a prefix
        ("Dobles vidas", "Dobles vidas"),
        ("Plain Title", "Plain Title"),
    ],
)
def test_format_suffix_stripped(title, expected):
    assert letterboxd.strip_format_suffix(title) == expected


def test_query_titles_prefers_original_then_falls_back():
    f = film(title="Sueños (sexo-amor) DOB", original_title="Drømmer")
    assert letterboxd.query_titles(f) == ["Drømmer", "Sueños (sexo-amor)"]


def test_query_titles_dedupes_when_both_titles_agree():
    f = film(title="Matrioshka", original_title="Matrioshka")
    assert letterboxd.query_titles(f) == ["Matrioshka"]


# ---------- what never gets looked up ----------

def test_truncated_programme_title_is_skipped():
    # Cineteca cuts at exactly 50 chars; every title that long is a shorts
    # programme, and the longest real film title in the data is 48.
    f = film(title="Hasta que Dios se acuerde de nosotros/Ensayo sobre")
    assert len(f["title"]) == letterboxd.TITLE_TRUNCATED_AT
    assert letterboxd.is_skippable(f)


def test_varios_director_is_skipped():
    assert letterboxd.is_skippable(film(title="Programa de cortos", director="Varios"))


def test_ordinary_film_is_not_skipped():
    assert not letterboxd.is_skippable(film(title="Moscas", director="Fernando Eimbcke"))


def test_skippable_film_never_reaches_the_network():
    def explode(_query):
        raise AssertionError("should not have searched")

    assert letterboxd.resolve_film(film(title="X", director="Varios"), explode) is None


# ---------- director agreement ----------

def test_accented_director_matches_unaccented():
    assert letterboxd.directors_agree(
        "Alejandro Iglesias Mendizábal", [{"name": "Alejandro Iglesias Mendizabal"}]
    )


def test_partial_surname_still_agrees():
    assert letterboxd.directors_agree("Johanné Gómez Terrero", [{"name": "Johanné Gómez"}])


def test_short_particles_cannot_carry_a_match():
    # "de"/"la" are shared by half the Spanish-language directory.
    assert not letterboxd.directors_agree("Ana de la Reguera", [{"name": "Luis de la Torre"}])


def test_missing_director_never_agrees():
    assert not letterboxd.directors_agree(None, [{"name": "Somebody"}])
    assert not letterboxd.directors_agree("Somebody", [])


# ---------- matching ----------

def test_rerelease_year_does_not_block_match():
    """Cars is filed by Cineteca under its 2026 re-release, Letterboxd under
    2006. Director plus runtime has to carry it."""
    f = film(
        title="Cars 20 aniversario DOB",
        original_title="Cars",
        director="John Lasseter",
        year=2026,
        duration_min=117,
    )
    c = candidate("Cars", year=2006, runtime=117, directors=["John Lasseter"])
    assert letterboxd.match_strength(f, c) == "strong"


def test_wrong_country_does_not_block_match():
    """Cineteca files The Alligator People as México. Country is never a signal."""
    f = film(
        title="El caimán humano",
        original_title="The Alligator People",
        director="Roy Del Ruth",
        country="México",
        year=1959,
        duration_min=74,
    )
    c = candidate(
        "The Alligator People", year=1959, runtime=74, directors=["Roy Del Ruth"]
    )
    assert letterboxd.match_strength(f, c) == "strong"


def test_spanish_title_matches_a_translated_letterboxd_name():
    f = film(
        title="Sopladora de hojas",
        original_title="Sopladora de hojas",
        director="Alejandro Iglesias Mendizábal",
        year=2015,
        duration_min=90,
    )
    c = candidate(
        "Leaf Blower",
        original="Sopladora de hojas",
        year=2015,
        runtime=90,
        directors=["Alejandro Iglesias Mendizabal"],
    )
    assert letterboxd.match_strength(f, c) == "strong"


def test_title_alone_is_never_enough():
    """A bare title collision with nothing to confirm it must not match."""
    f = film(title="Resurrection", original_title="Resurrection")
    c = candidate("Resurrection", year=1999, runtime=108, directors=["Someone Else"])
    assert letterboxd.match_strength(f, c) is None


def test_film_without_metadata_never_matches():
    """"Sin embargo, una utopía" has no director, year or runtime at all. The
    near-identical Portuguese title below is a different film, and nothing on
    the record could tell us so — which is exactly why it has to be rejected."""
    f = film(title="Sin embargo, una utopía")
    c = candidate("Sin embargo, uma utopia", year=2024, directors=["Fabiana Parra"])
    assert letterboxd.match_strength(f, c) is None
    assert letterboxd.pick_match(f, [c]) is None


def test_wrong_director_rejected():
    f = film(title="Simbionte", original_title="Simbionte", director="Bryan Mura", year=2023)
    candidates = [
        candidate("Symbiont", year=2019, directors=["Lorenzo Valle"]),
        candidate("Symbiont", year=2025, directors=["Louis Samuel Corneil"]),
    ]
    assert letterboxd.pick_match(f, candidates) is None


def test_strong_match_wins_over_an_earlier_weak_one():
    """"Lo que queda de ti": the Spanish title's top hit is a different film
    with a matching year, and the real one is second. Rank must not decide."""
    f = film(
        title="Lo que queda de ti",
        original_title="Allly baqi mink",
        director="Cherien Dabis",
        year=2025,
        duration_min=145,
    )
    decoy = candidate(
        "The Remnants of You",
        original="Lo que queda de ti",
        year=2025,
        runtime=100,
        directors=["Gala Gracia"],
    )
    real = candidate(
        "All That's Left of You", year=2025, runtime=145, directors=["Cherien Dabis"]
    )
    assert letterboxd.match_strength(f, decoy) == "ok"
    assert letterboxd.pick_match(f, [decoy, real]) is real


def test_resolve_falls_back_to_the_spanish_title():
    f = film(
        title="Lo que queda de ti",
        original_title="Allly baqi mink",
        director="Cherien Dabis",
        year=2025,
        duration_min=145,
    )
    real = candidate(
        "All That's Left of You", year=2025, runtime=145, directors=["Cherien Dabis"]
    )
    # First query (the transliterated original) finds nothing; second one does.
    resolved = letterboxd.resolve_film(f, fetch_returning([], [real]))
    assert resolved["url"] == real["link"]


# ---------- what comes back ----------

def test_rating_is_rounded_to_one_decimal():
    """The raw average drifts constantly; rounding to what the badge shows is
    what keeps an unchanged cartelera a one-line diff."""
    f = film(title="Sopladora de hojas", director="Alejandro Iglesias Mendizábal", year=2015)
    c = candidate(
        "Leaf Blower", year=2015, directors=["Alejandro Iglesias Mendizabal"],
        rating=3.5057348700482547,
    )
    assert letterboxd.resolve_film(f, fetch_returning([c])) == {
        "url": c["link"],
        "rating": 3.5,
    }


def test_unrated_film_yields_no_badge():
    """Letterboxd withholds the average below a threshold of ratings. A
    confident match with no score is still no badge."""
    f = film(title="Oh, fortuna", director="Luis Ayhllón", year=2026, duration_min=95)
    c = candidate(
        "Oh, fortuna", year=2026, runtime=95, directors=["Luis Ayhllón"], rating=None
    )
    assert letterboxd.resolve_film(f, fetch_returning([c])) is None


def test_no_results_yields_no_badge():
    f = film(title="Animanimales", director="Julia Ocker")
    assert letterboxd.resolve_film(f, fetch_returning([], [])) is None


def test_lookup_failure_propagates():
    """resolve_film must raise rather than return None on an outage — the
    caller tells those apart to decide whether to keep the previous badge."""
    def boom(_query):
        raise RuntimeError("letterboxd is down")

    with pytest.raises(RuntimeError):
        letterboxd.resolve_film(film(title="Moscas", director="Fernando Eimbcke"), boom)


def test_search_films_ignores_non_film_items():
    payload = {
        "items": [
            {"type": "ContributorSearchItem", "contributor": {"name": "x"}},
            {"type": "FilmSearchItem", "film": candidate("Moscas")},
        ]
    }
    films = letterboxd.search_films("Moscas", lambda _q: payload)
    assert [f["name"] for f in films] == ["Moscas"]
